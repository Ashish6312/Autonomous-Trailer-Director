"""Run the evaluation scenarios through the repair loop and record per-run metrics.

No aggregate score. Each scenario runs twice; ``replay_match`` compares the
two records with the timestamp excluded. Recordings under ``live_dir`` are
replayed as ``L01_recorded_live_plan`` rows.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trailer_director.constraints import ConstraintContext
from trailer_director.domain import AudienceType, EpisodePackage
from trailer_director.domain.base import DomainModel
from trailer_director.evaluation.scenarios import DEFAULT_FIXTURE_DIR, SCENARIOS, Scenario, Setup, _setup
from trailer_director.planning import IssueCode, ReplayPlanner
from trailer_director.planning.pipeline import campaign_context
from trailer_director.repair import (
    AttemptKind,
    Component,
    DeterministicRepairPlanner,
    LoopStatus,
    RepairRun,
    ReplayRepairPlanner,
)
from trailer_director.repair.models import ClipAction
from trailer_director.repair.verification import clip_changes

EVALUATION_FORMAT_VERSION = 1
FIXED_NOW = datetime(2026, 9, 30, tzinfo=UTC)
"""Timestamp given to every evaluated run, so the evaluation output is byte-for-byte reproducible."""
LIVE_SCENARIO_ID = "L01_recorded_live_plan"


class ScenarioResult(DomainModel):
    scenario_id: str
    title: str
    audience: AudienceType
    mode: str
    repair_mode: str
    initial_status: str
    final_status: LoopStatus
    violation_count: int
    """Blocking and review findings (errors + warnings) on the initial plan, or its error-level planning issues."""
    reason_codes: list[str]
    final_reason_codes: list[str]
    repair_attempts: int
    planner_calls: int
    repair_calls: int
    fallback_used: bool
    fallbacks: list[str]
    changed_components: list[str]
    preserved_components: list[str]
    estimated_cost: float
    currency: str
    input_tokens: int | None
    output_tokens: int | None
    replay_match: bool
    as_expected: bool
    deviations: list[str]
    notes: list[str] = []


class EvaluationReport(DomainModel):
    format_version: int
    episode_id: str
    evidence_fingerprint: str
    scenario_count: int
    run_count: int
    unexpected: list[str]
    not_applicable: list[str]
    """Scenario/audience pairs the dataset cannot set up, with the reason."""
    results: list[ScenarioResult]


def evaluate(
    evidence: EpisodePackage,
    audiences: list[AudienceType] | None = None,
    scenario_ids: list[str] | None = None,
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    live_dir: Path | None = None,
) -> EvaluationReport:
    audiences = audiences or list(AudienceType)
    chosen = [s for s in SCENARIOS if not scenario_ids or s.scenario_id in scenario_ids]
    results = [
        _evaluate_one(evidence, scenario, audience, fixture_dir)
        for scenario in chosen
        for audience in audiences
        if audience in scenario.audiences
    ]
    if live_dir is not None and (not scenario_ids or LIVE_SCENARIO_ID in scenario_ids):
        results += [
            _evaluate_live(evidence, audience, live_dir)
            for audience in audiences
            if (live_dir / "planner_runs" / f"{audience}.json").exists()
        ]
    fingerprint = evidence.fingerprint()
    return EvaluationReport(
        format_version=EVALUATION_FORMAT_VERSION,
        episode_id=evidence.episode.episode_id,
        evidence_fingerprint=fingerprint,
        scenario_count=len({r.scenario_id for r in results}),
        run_count=len(results),
        unexpected=[f"{r.scenario_id}/{r.audience}" for r in results if not r.as_expected],
        not_applicable=[
            f"{s.scenario_id}/{audience}: {s.not_applicable}"
            for s in chosen
            for audience in audiences
            if audience not in s.audiences
        ],
        results=results,
    )


def _evaluate_one(
    evidence: EpisodePackage, scenario: Scenario, audience: AudienceType, fixture_dir: Path
) -> ScenarioResult:
    setup = scenario.build(evidence, audience, fixture_dir)
    first, notes = _execute(setup)
    second, _ = _execute(scenario.build(evidence, audience, fixture_dir))
    deviations = scenario.expect(first, audience) + (scenario.inspect(setup) if scenario.inspect else [])
    return _result(scenario.scenario_id, scenario.title, audience, setup, first, second, deviations, notes)


def _evaluate_live(evidence: EpisodePackage, audience: AudienceType, live_dir: Path) -> ScenarioResult:
    def build() -> Setup:
        repair_file = live_dir / "repair_runs" / f"{audience}.json"
        repairer = ReplayRepairPlanner(live_dir / "repair_runs") if repair_file.exists() else None
        return _setup(
            evidence,
            campaign_context(evidence, audience),
            ReplayPlanner(live_dir / "planner_runs"),
            repairer or DeterministicRepairPlanner(evidence),
            planner_label="replay (recorded live)",
        )

    setup = build()
    first, notes = _execute(setup)
    second, _ = _execute(build())
    problems = []
    initial = first.attempts[0] if first.attempts else None
    if initial is None or not initial.planner_version.startswith("llm-"):
        problems.append("recording is not a live model response")
    mismatch = [i for a in first.attempts for i in a.planning_issues if i.code is IssueCode.REPLAY_EVIDENCE_MISMATCH]
    if mismatch:
        problems.append("recording was made against different evidence")
    if first.status is not LoopStatus.ACCEPTED:
        problems.append(f"expected accepted, got {first.status}")
    notes.append(f"recorded by {initial.planner_version if initial else '?'}; no API call made")
    title = "recorded live plan, replayed"
    return _result(LIVE_SCENARIO_ID, title, audience, setup, first, second, problems, notes)


def _execute(setup: Setup) -> tuple[RepairRun, list[str]]:
    run = setup.loop.run(setup.planner, setup.context, FIXED_NOW)
    notes: list[str] = []
    if setup.replan_to is None and setup.replan_loop is None:
        return run, notes
    target = setup.replan_to or setup.context
    change = setup.change or f"conditions changed to {_where(target)}"
    notes.append(f"plan for {_where(setup.context)}: {run.status}; {change}")
    if run.status is not LoopStatus.ACCEPTED:
        return run, notes
    loop = setup.replan_loop or setup.loop
    return loop.replan(run.final_proposal, target, FIXED_NOW), notes


def _where(context: ConstraintContext) -> str:
    return f"{context.evaluation_date} {context.territory}"


def _result(
    scenario_id: str,
    title: str,
    audience: AudienceType,
    setup: Setup,
    run: RepairRun,
    rerun: RepairRun,
    deviations: list[str],
    notes: list[str],
) -> ScenarioResult:
    initial = next((a for a in run.attempts if a.proposal is not None or a.status == "invalid_output"), None)
    codes: set[str] = set()
    count = 0
    if initial is not None and initial.eligibility is not None:
        findings = [v for v in initial.eligibility.violations if v.severity != "info"]
        codes, count = {v.reason_code for v in findings}, len(findings)
    elif initial is not None:
        errors = [i for i in initial.planning_issues if i.severity == "error"]
        codes, count = {i.code for i in errors}, len(errors)
    final_codes = (
        {v.reason_code for v in run.final_eligibility.violations if v.severity != "info"}
        if run.final_eligibility
        else set()
    )
    changed, preserved = _components(initial.proposal if initial else None, run)
    usage = [a.usage for a in run.attempts if a.usage is not None]
    return ScenarioResult(
        scenario_id=scenario_id,
        title=title,
        audience=audience,
        mode=setup.planner_label,
        repair_mode=setup.repairer_label,
        initial_status=initial.status if initial else "planner_failed",
        final_status=run.status,
        violation_count=count,
        reason_codes=sorted(codes),
        final_reason_codes=sorted(final_codes),
        repair_attempts=run.budget.repair_attempts,
        planner_calls=sum(a.model_calls for a in run.attempts if a.kind is not AttemptKind.REPAIR),
        repair_calls=sum(a.model_calls for a in run.attempts if a.kind is AttemptKind.REPAIR),
        fallback_used=bool(run.fallbacks),
        fallbacks=run.fallbacks,
        changed_components=changed,
        preserved_components=preserved,
        estimated_cost=run.budget.estimated_cost,
        currency=run.budget.currency,
        input_tokens=sum(u.input_tokens for u in usage) if usage else None,
        output_tokens=sum(u.output_tokens for u in usage) if usage else None,
        replay_match=_canonical(run) == _canonical(rerun),
        as_expected=not deviations,
        deviations=deviations,
        notes=notes,
    )


def _components(initial, run: RepairRun) -> tuple[list[str], list[str]]:
    """Component-level diff between the first plan and the accepted one."""
    final = run.final_proposal
    if initial is None or final is None:
        return [], []
    changed, preserved = [], []
    for change in clip_changes(initial, final):
        if change.action in (ClipAction.DROPPED, ClipAction.ADDED):
            changed.append(f"{change.clip_id}.({change.action})")
            continue
        changed += [f"{change.clip_id}.{component}" for component in change.changed]
        preserved += [f"{change.clip_id}.{component}" for component in Component if component not in change.changed]
    return changed, preserved


def _canonical(run: RepairRun) -> dict[str, Any]:
    dump = run.model_dump(mode="json", exclude={"created_at"})
    for attempt in dump["attempts"]:
        attempt.pop("latency_seconds", None)
    return dump
