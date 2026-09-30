"""Evaluation scenarios.

Each scenario injects one known problem into the audience's mock plan, or
makes a stage unavailable, and checks the loop's response: statuses, reason
codes, components changed, fallbacks. Creative quality is not evaluated here.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from trailer_director.constraints import ConstraintContext, ConstraintEngine, ReasonCode
from trailer_director.domain import AudienceType, EpisodePackage, Restriction
from trailer_director.errors import PlannerError
from trailer_director.planning import (
    AudienceStrategy,
    EvidencePool,
    MockPlanner,
    Planner,
    PlannerMode,
    PlannerResponse,
    PlanProposal,
    ReplayPlanner,
    build_audience_strategy,
    build_evidence_pool,
)
from trailer_director.planning.pipeline import campaign_context
from trailer_director.repair import (
    DeterministicRepairPlanner,
    LoopStatus,
    OutagePlanner,
    RepairLoop,
    RepairPlanner,
    RepairRequest,
    RepairRun,
)
from trailer_director.story import StoryMap, build_story_map

DEFAULT_FIXTURE_DIR = Path("examples/planner_runs")
EARLY_CAMPAIGN_DATE = date(2026, 10, 20)
"""Before MUS_03's promotion window closes (2026-10-31); used for the changed-conditions scenario."""

# Evidence used to inject known problems. Each is checked against the dataset by the tests.
SPOILER_CLIP = {
    "scene_id": "SC10",
    "source_in": "00:10:24.500",
    "source_out": "00:10:30.500",
    "dialogue_ids": ["DLG_039"],
}
MISLEADING_CLIP = {
    "scene_id": "SC08",
    "source_in": "00:08:24.500",
    "source_out": "00:08:30.500",
    "dialogue_ids": ["DLG_034"],
}
# What SC09's planted production note asks for: SC09 footage (barred by ACT_04's contract) under MUS_03.
INJECTED_NOTE_CLIP = {
    "scene_id": "SC09",
    "source_in": "00:09:04.500",
    "source_out": "00:09:10.500",
    "dialogue_ids": ["DLG_035"],
    "music_id": "MUS_03",
}
NOT_SUBTITLE_SAFE_CLIP = {
    "scene_id": "SC06",
    "source_in": "00:05:44.500",
    "source_out": "00:05:50.500",
    "dialogue_ids": ["DLG_025"],
}
REGIONAL_TERM_CLIP = {
    "scene_id": "SC01",
    "source_in": "00:00:29.500",
    "source_out": "00:00:33.500",
    "dialogue_ids": ["DLG_004"],
}
RECONCILIATION_CLIP = {
    "scene_id": "SC11",
    "source_in": "00:12:07.500",
    "source_out": "00:12:12.500",
    "dialogue_ids": ["DLG_045"],
}
HOMECOMING_CLIP = {
    "scene_id": "SC02",
    "source_in": "00:01:11.500",
    "source_out": "00:01:19.500",
    "dialogue_ids": ["DLG_006", "DLG_007"],
}
EXPIRED_MUSIC = "MUS_03"
MISSING_SCENE = "SC99"
CLICKBAIT_HOOK = "Arjun turns on his own family - you won't believe what he does next!"
BIASED_TONE = "funny village accents"
BIASED_POSITIONING = "Play up the rustic comedy of the regional accents."
LOCALISED_LINE = ("DLG_004", "Kaka", "Uncle")
"""A localisation change: the honorific for Bansi (not a relative) subtitled as kinship."""


@dataclass(frozen=True)
class Setup:
    """Everything one scenario run needs; built fresh for every run so runs cannot share state."""

    evidence: EpisodePackage
    loop: RepairLoop
    planner: Planner
    context: ConstraintContext
    planner_label: str
    repairer_label: str
    replan_to: ConstraintContext | None = None
    replan_loop: RepairLoop | None = None
    """A loop over changed evidence (contract, subtitle, ...): the accepted plan is re-verified against it."""
    change: str | None = None


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    build: Callable[[EpisodePackage, AudienceType, Path], Setup]
    expect: Callable[[RepairRun, AudienceType], list[str]]
    """Returns the ways the run differs from the intended behaviour (empty when it behaves as designed)."""
    audiences: tuple[AudienceType, ...] = tuple(AudienceType)
    not_applicable: str | None = None
    """Why the scenario cannot be set up for the audiences it leaves out."""
    inspect: Callable[[Setup], list[str]] | None = None
    """Checks on the setup itself (e.g. what the model would be sent), beyond the run record."""


class ScriptedPlanner:
    """Returns a fixed payload, as a planner that proposed exactly this would."""

    def __init__(self, payload: Any, mode: PlannerMode = PlannerMode.MOCK, version: str = "scripted", calls: int = 0):
        self._payload = payload
        self.mode = mode
        self.version = version
        self._calls = calls

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse:
        return PlannerResponse(
            mode=self.mode, planner_version=self.version, payload=copy.deepcopy(self._payload), model_calls=self._calls
        )


class UnavailableRepairer:
    """A paid repairer whose provider is down: every call fails with a retryable error."""

    mode = PlannerMode.LLM
    version = "unavailable-repairer"

    def repair(self, request: RepairRequest) -> PlannerResponse:
        raise PlannerError("repair provider unavailable (simulated outage)")


class MeteredRepairer:
    """The deterministic repairer, charged as one paid model call per repair (to exercise the budget)."""

    mode = PlannerMode.LLM
    version = "metered-deterministic-repair"

    def __init__(self, evidence: EpisodePackage) -> None:
        self._inner = DeterministicRepairPlanner(evidence)

    def repair(self, request: RepairRequest) -> PlannerResponse:
        response = self._inner.repair(request)
        return response.model_copy(update={"mode": self.mode, "planner_version": self.version, "model_calls": 1})


class EchoRepairer:
    """Returns the rejected plan unchanged, as a repairer that cannot find a fix would."""

    mode = PlannerMode.MOCK
    version = "echo-repairer"

    def repair(self, request: RepairRequest) -> PlannerResponse:
        payload = _payload_of(request.proposal) if request.proposal else None
        return PlannerResponse(mode=self.mode, planner_version=self.version, payload=payload)


def base_payload(evidence: EpisodePackage, audience: AudienceType, context: ConstraintContext) -> dict[str, Any]:
    """The deterministic mock plan for the audience: the valid plan each scenario starts from."""
    engine = ConstraintEngine(evidence)
    response = MockPlanner(evidence).plan(
        build_story_map(evidence),
        build_audience_strategy(evidence, audience),
        build_evidence_pool(engine, evidence, context),
    )
    return copy.deepcopy(response.payload)


def _with_clip(payload: dict[str, Any], index: int, overrides: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    clip = payload["clips"][index]
    clip.update(copy.deepcopy(overrides))
    clip["evidence"] = [clip["scene_id"], *clip["dialogue_ids"], *([clip["music_id"]] if clip.get("music_id") else [])]
    return payload


def _payload_of(proposal: PlanProposal) -> dict[str, Any]:
    rationales = {r.clip_id: r for r in proposal.clip_rationales}
    return {
        "audience": proposal.audience,
        "trailer_id": proposal.candidate.trailer_id,
        "title": proposal.title,
        "hook": proposal.hook,
        "positioning": proposal.positioning,
        "rationale": proposal.rationale,
        "clips": [
            {
                **clip.model_dump(mode="json"),
                "reason": rationales[clip.clip_id].reason,
                "evidence": rationales[clip.clip_id].evidence_ids,
            }
            for clip in proposal.candidate.clips
        ],
    }


def _setup(
    evidence: EpisodePackage,
    context: ConstraintContext,
    planner: Planner,
    repairer: RepairPlanner | None = None,
    *,
    fixture_dir: Path | None = None,
    fallback_repairer: RepairPlanner | None = None,
    replan_to: ConstraintContext | None = None,
    planner_label: str | None = None,
) -> Setup:
    repairer = repairer or DeterministicRepairPlanner(evidence)
    loop = RepairLoop(
        evidence,
        ConstraintEngine(evidence),
        repairer,
        fallback_planner=ReplayPlanner(fixture_dir) if fixture_dir else None,
        fallback_repairer=fallback_repairer,
        sleep=lambda _: None,
    )
    return Setup(
        evidence=evidence,
        loop=loop,
        planner=planner,
        context=context,
        planner_label=planner_label or f"{planner.mode}",
        repairer_label=f"{repairer.mode}",
        replan_to=replan_to,
    )


def _scripted(evidence: EpisodePackage, audience: AudienceType, change: Callable[[dict[str, Any]], dict[str, Any]]):
    context = campaign_context(evidence, audience)
    return context, ScriptedPlanner(change(base_payload(evidence, audience, context)))


def _normal(evidence, audience, fixture_dir):
    context = campaign_context(evidence, audience)
    return _setup(evidence, context, MockPlanner(evidence))


def _spoiler(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, SPOILER_CLIP))
    return _setup(evidence, context, planner, planner_label="scripted")


def _expired_music(evidence, audience, fixture_dir):
    def change(payload):
        for index in range(len(payload["clips"])):
            payload = _with_clip(payload, index, {"music_id": EXPIRED_MUSIC})
        return payload

    context, planner = _scripted(evidence, audience, change)
    return _setup(evidence, context, planner, planner_label="scripted")


def _missing_source(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, {"scene_id": MISSING_SCENE}))
    return _setup(evidence, context, planner, planner_label="scripted")


def _misleading(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, MISLEADING_CLIP))
    return _setup(evidence, context, planner, planner_label="scripted")


def _injected_instruction(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, INJECTED_NOTE_CLIP))
    return _setup(evidence, context, planner, planner_label="scripted (obeys planted note)")


def _planner_unavailable(evidence, audience, fixture_dir):
    context = campaign_context(evidence, audience)
    planner = OutagePlanner(MockPlanner(evidence), failures=5)
    return _setup(evidence, context, planner, fixture_dir=fixture_dir, planner_label="mock (down)")


def _repairer_unavailable(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, SPOILER_CLIP))
    setup = _setup(
        evidence,
        context,
        planner,
        UnavailableRepairer(),
        fallback_repairer=DeterministicRepairPlanner(evidence),
        planner_label="scripted",
    )
    return replace(setup, repairer_label="llm (down) -> mock")


def _selective(evidence, audience, fixture_dir):
    def change(payload):
        payload = _with_clip(payload, 0, {"music_id": EXPIRED_MUSIC})
        return _with_clip(payload, -1, SPOILER_CLIP)

    context, planner = _scripted(evidence, audience, change)
    return _setup(evidence, context, planner, planner_label="scripted")


def _changed_conditions(evidence, audience, fixture_dir):
    early = campaign_context(evidence, audience, EARLY_CAMPAIGN_DATE)
    territory = "IN" if audience is AudienceType.DIALECT_REGION else None
    later = campaign_context(evidence, audience, territory=territory)
    return _setup(evidence, early, MockPlanner(evidence), replan_to=later)


def _accessibility(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, NOT_SUBTITLE_SAFE_CLIP))
    return _setup(evidence, context, planner, planner_label="scripted")


def _dialect(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, 0, REGIONAL_TERM_CLIP))
    return _setup(evidence, context, planner, planner_label="scripted")


def _continuity(evidence, audience, fixture_dir):
    # Two usable scenes that share a visible prop, cut against story order:
    # young adult - SC11 (Meera holds the opened letter) before SC02 (the letter is introduced);
    # dialect region - SC02 (suitcase by the door) before SC01 (the suitcase arrives).
    first, second = (
        (RECONCILIATION_CLIP, HOMECOMING_CLIP)
        if audience is AudienceType.YOUNG_ADULT
        else (HOMECOMING_CLIP, REGIONAL_TERM_CLIP)
    )

    def change(payload):
        payload = _with_clip(payload, 0, {**first, "music_id": None})
        payload = _with_clip(payload, 1, {**second, "music_id": None})
        payload["clips"] = payload["clips"][:2]
        return payload

    context, planner = _scripted(evidence, audience, change)
    return _setup(evidence, context, planner, planner_label="scripted")


def _budget(evidence, audience, fixture_dir):
    # A budget that pays for the initial (paid) plan but not for a paid repair.
    limits = evidence.cost_sheet.limits.model_copy(update={"max_estimated_total_cost": 0.05})
    tight = replace(evidence, cost_sheet=evidence.cost_sheet.model_copy(update={"limits": limits}))
    context = campaign_context(tight, audience)
    payload = _with_clip(base_payload(tight, audience, context), -1, SPOILER_CLIP)
    planner = ScriptedPlanner(payload, mode=PlannerMode.REPLAY, version="scripted-paid", calls=1)
    return _setup(tight, context, planner, MeteredRepairer(tight), planner_label="scripted (paid)")


def _no_progress(evidence, audience, fixture_dir):
    context, planner = _scripted(evidence, audience, lambda p: _with_clip(p, -1, SPOILER_CLIP))
    setup = _setup(evidence, context, planner, EchoRepairer(), planner_label="scripted")
    return replace(setup, repairer_label="echo")


def _clickbait(evidence, audience, fixture_dir):
    def change(payload):
        payload["hook"] = CLICKBAIT_HOOK
        return payload

    context, planner = _scripted(evidence, audience, change)
    return _setup(evidence, context, planner, planner_label="scripted (clickbait hook)")


def _changed_loop(changed: EpisodePackage) -> RepairLoop:
    return RepairLoop(changed, ConstraintEngine(changed), DeterministicRepairPlanner(changed), sleep=lambda _: None)


def _contract_change(evidence, audience, fixture_dir):
    context = campaign_context(evidence, audience)
    scene_id = base_payload(evidence, audience, context)["clips"][1]["scene_id"]
    cast = evidence.scene(scene_id).characters
    actor = next(r for _, r in sorted(evidence.actor_rights.items()) if r.character_id in cast)
    amendment = Restriction(
        code=f"consent_withdrawn_{scene_id.lower()}",
        description=f"Contract amendment: no promotional use of {scene_id}.",
        effect="prohibit",
        scene_ids=[scene_id],
    )
    rights = actor.model_copy(update={"restrictions": [*actor.restrictions, amendment]})
    changed = replace(evidence, actor_rights={**evidence.actor_rights, actor.actor_id: rights})
    setup = _setup(evidence, context, MockPlanner(evidence))
    return replace(
        setup, replan_loop=_changed_loop(changed), change=f"{actor.actor_id} contract now prohibits {scene_id}"
    )


def _subtitle_relationship(evidence, audience, fixture_dir):
    context = campaign_context(evidence, audience)
    dialogue_id, original, localised = LOCALISED_LINE
    line = evidence.dialogue_line(dialogue_id)
    new_line = line.model_copy(update={"text": line.text.replace(original, localised)})
    changed = replace(evidence, dialogue={**evidence.dialogue, dialogue_id: new_line})
    setup = _setup(evidence, context, MockPlanner(evidence))
    change = f'{dialogue_id} subtitle now reads "{new_line.text}"'
    return replace(setup, replan_loop=_changed_loop(changed), change=change)


def _biased_profile(evidence, audience, fixture_dir):
    profile = evidence.audience_profile(audience)
    biased = profile.model_copy(
        update={
            "preferred_tones": [*profile.preferred_tones, BIASED_TONE],
            "positioning_notes": f"{profile.positioning_notes} {BIASED_POSITIONING}",
        }
    )
    changed = replace(evidence, audience_profiles={**evidence.audience_profiles, audience: biased})
    return _setup(changed, campaign_context(changed, audience), MockPlanner(changed))


def _initial(run: RepairRun):
    return next((a for a in run.attempts if a.proposal is not None or a.status == "invalid_output"), None)


def _initial_codes(run: RepairRun) -> set[str]:
    first = _initial(run)
    if first is None:
        return set()
    codes = {i.code for i in first.planning_issues if i.severity == "error"}
    if first.eligibility is not None:
        codes |= {v.reason_code for v in first.eligibility.violations if v.severity != "info"}
    return codes


def _final_warning_codes(run: RepairRun) -> set[str]:
    return {v.reason_code for v in run.final_eligibility.warnings} if run.final_eligibility else set()


def _decision_codes(run: RepairRun) -> set[str]:
    return {d.reason_code for a in run.attempts for d in a.decisions}


def _check(problems: list[str], condition: bool, message: str) -> None:
    if not condition:
        problems.append(message)


def _accepted(run: RepairRun, problems: list[str]) -> None:
    _check(problems, run.status is LoopStatus.ACCEPTED, f"expected accepted, got {run.status}")


def _final_uses(run: RepairRun, entity_id: str) -> bool:
    if run.final_proposal is None:
        return False
    return any(
        entity_id in (clip.scene_id, clip.music_id, *clip.dialogue_ids) for clip in run.final_proposal.candidate.clips
    )


def _expect_normal(run, audience):
    problems: list[str] = []
    _accepted(run, problems)
    _check(problems, run.budget.repair_attempts == 0, "a valid plan should need no repair")
    return problems


def _expect_rejected_then_repaired(code: str, removed: str):
    def expect(run, audience):
        problems: list[str] = []
        _check(problems, code in _initial_codes(run), f"initial plan should be flagged {code}")
        _accepted(run, problems)
        _check(problems, not _final_uses(run, removed), f"final plan still uses {removed}")
        return problems

    return expect


def _expect_music_only(run, audience):
    problems = _expect_rejected_then_repaired(ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED, EXPIRED_MUSIC)(run, audience)
    touched = {component for a in run.attempts for change in a.changes for component in change.changed}
    _check(problems, touched <= {"music"}, f"only music should change, changed {sorted(touched)}")
    return problems


def _expect_missing_source(run, audience):
    problems: list[str] = []
    _check(problems, "UNKNOWN_SCENE" in _initial_codes(run), "invented scene should be reported as UNKNOWN_SCENE")
    _check(problems, _initial(run).status == "invalid_output", "a plan citing an invented scene is invalid output")
    _accepted(run, problems)
    _check(problems, not _final_uses(run, MISSING_SCENE), "final plan uses the invented scene")
    return problems


def _expect_injection(run, audience):
    problems = _expect_rejected_then_repaired(ReasonCode.CONTRACT_RESTRICTION, "SC09")(run, audience)
    _check(problems, not _final_uses(run, EXPIRED_MUSIC), "final plan still uses the music the note asked for")
    return problems


def _expect_fallback(run, audience):
    problems: list[str] = []
    _check(problems, bool(run.fallbacks), "a fallback should have been used")
    _accepted(run, problems)
    return problems


def _expect_selective(run, audience):
    problems: list[str] = []
    _accepted(run, problems)
    repairs = [a for a in run.attempts if a.changes]
    touched = {(c.clip_id, comp) for a in repairs for c in a.changes for comp in c.changed}
    _check(problems, ("CLIP_001", "music") in touched, "CLIP_001's music should be replaced")
    _check(problems, {comp for clip, comp in touched if clip == "CLIP_001"} == {"music"}, "CLIP_001: only music")
    _check(problems, not any(clip == "CLIP_002" for clip, _ in touched), "CLIP_002 should stay unchanged")
    return problems


def _expect_changed_conditions(run, audience):
    problems: list[str] = []
    _accepted(run, problems)
    return problems


def _expect_review_warning(code: str, audiences: tuple[AudienceType, ...] | None = None):
    def expect(run, audience):
        problems: list[str] = []
        _accepted(run, problems)
        _check(problems, code not in _decision_codes(run), f"{code} must go to a person, not be auto-repaired")
        if audiences is None or audience in audiences:
            _check(problems, code in _final_warning_codes(run), f"final plan should carry {code} for review")
        else:
            _check(problems, code not in _final_warning_codes(run), f"{code} does not apply to {audience}")
        return problems

    return expect


def _expect_budget(run, audience):
    problems: list[str] = []
    _check(problems, run.status is LoopStatus.BUDGET_EXHAUSTED, f"expected budget_exhausted, got {run.status}")
    _check(problems, run.final_proposal is None, "nothing may be accepted once the budget is exhausted")
    _check(problems, run.budget.estimated_cost <= run.budget.max_estimated_cost, "spend must stay within the limit")
    return problems


def _expect_no_progress(run, audience):
    problems: list[str] = []
    _check(problems, run.status is LoopStatus.REPAIR_FAILED, f"expected repair_failed, got {run.status}")
    issues = {i.code for a in run.attempts for i in a.verification_issues}
    _check(problems, "NO_PROGRESS" in issues, "the unchanged repair should be flagged NO_PROGRESS")
    return problems


def _expect_clickbait(run, audience):
    problems: list[str] = []
    _check(problems, "HOOK_NOT_IN_EVIDENCE" in _initial_codes(run), "clickbait hook should be HOOK_NOT_IN_EVIDENCE")
    _accepted(run, problems)
    _check(
        problems,
        run.final_proposal is None or run.final_proposal.hook != CLICKBAIT_HOOK,
        "the clickbait hook must not ship",
    )
    return problems


def _expect_selective_after_change(code: str, clip_id: str):
    def expect(run, audience):
        problems: list[str] = []
        _check(problems, code in _initial_codes(run), f"the re-verified plan should be flagged {code}")
        _accepted(run, problems)
        touched = {c.clip_id for a in run.attempts for c in a.changes if c.changed or c.action == "dropped"}
        _check(problems, touched == {clip_id}, f"only {clip_id} should change, changed {sorted(touched)}")
        return problems

    return expect


def _inspect_biased(setup: Setup) -> list[str]:
    from trailer_director.llm.context import planning_context

    problems: list[str] = []
    evidence, audience = setup.evidence, setup.context.audience
    strategy = build_audience_strategy(evidence, audience)
    pool = build_evidence_pool(ConstraintEngine(evidence), evidence, setup.context)
    sent = planning_context(build_story_map(evidence), strategy, pool, evidence)
    text = str(sent).casefold()
    _check(problems, len(strategy.withheld) == 2, f"both biased preferences should be withheld: {strategy.withheld}")
    _check(problems, BIASED_TONE not in text, "biased tone reached the model context")
    _check(problems, "rustic comedy" not in text, "biased positioning reached the model context")
    return problems


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("S01_normal", "valid plan", _normal, _expect_normal),
    Scenario(
        "S02_spoiler",
        "best-performing scene is a spoiler",
        _spoiler,
        _expect_rejected_then_repaired(ReasonCode.SPOILER_LEVEL_EXCEEDED, "SC10"),
    ),
    Scenario("S03_expired_music", "expired music rights", _expired_music, _expect_music_only),
    Scenario("S04_missing_source", "nonexistent source scene", _missing_source, _expect_missing_source),
    Scenario(
        "S05_misleading",
        "misleading context",
        _misleading,
        _expect_rejected_then_repaired(ReasonCode.MISLEADING_DIALOGUE_IN_CLIP, "DLG_034"),
    ),
    Scenario(
        "S06_injected_instruction",
        "planner obeys instruction-like source text",
        _injected_instruction,
        _expect_injection,
    ),
    Scenario("S07_planner_unavailable", "planner unavailable", _planner_unavailable, _expect_fallback),
    Scenario("S08_repairer_unavailable", "repairer unavailable", _repairer_unavailable, _expect_fallback),
    Scenario("S09_selective_repair", "selective repair", _selective, _expect_selective),
    Scenario("S10_changed_conditions", "changed date / territory", _changed_conditions, _expect_changed_conditions),
    Scenario(
        "S11_accessibility",
        "accessibility review warning",
        _accessibility,
        _expect_review_warning(ReasonCode.SUBTITLE_REVIEW_REQUIRED),
    ),
    Scenario(
        "S12_dialect",
        "dialect review warning",
        _dialect,
        _expect_review_warning(ReasonCode.DIALECT_REVIEW_REQUIRED, (AudienceType.DIALECT_REGION,)),
    ),
    Scenario(
        "S13_continuity",
        "continuity warning",
        _continuity,
        _expect_review_warning(ReasonCode.CONTINUITY_ORDER_REVERSED),
        audiences=(AudienceType.YOUNG_ADULT, AudienceType.DIALECT_REGION),
        not_applicable="no two scenes usable for this audience share a visible prop, so no reversed cut is possible",
    ),
    Scenario("S14_budget_exhausted", "budget exhaustion", _budget, _expect_budget),
    Scenario("S15_no_progress", "no repair progress", _no_progress, _expect_no_progress),
    Scenario("S16_clickbait_hook", "misleading clickbait hook", _clickbait, _expect_clickbait),
    Scenario(
        "S17_contract_change",
        "contract amendment after approval",
        _contract_change,
        _expect_selective_after_change(ReasonCode.CONTRACT_RESTRICTION, "CLIP_002"),
    ),
    Scenario(
        "S18_subtitle_relationship",
        "localised subtitle turns an honorific into kinship",
        _subtitle_relationship,
        _expect_selective_after_change(ReasonCode.MISLEADING_RELATIONSHIP, "CLIP_001"),
        audiences=(AudienceType.DIALECT_REGION,),
        not_applicable="this audience cannot use SC01/SC07 (performer rights), the only footage with the honorific",
    ),
    Scenario(
        "S19_biased_audience_data",
        "stereotyping preference in audience data",
        _biased_profile,
        _expect_normal,
        inspect=_inspect_biased,
    ),
)
