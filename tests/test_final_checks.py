"""Checks for change cases: hook truth, relationship truth, audience bias, verdicts,
run limits, the constraint catalogue and the exported artifacts."""

import json
import socket
from dataclasses import replace

import pytest
from support import DATA_DIR, FIXED_NOW, REPLAY_DIR, clip, context, record

from trailer_director.artifacts import build_trailer, write_artifacts
from trailer_director.cli import main
from trailer_director.config import ConfigError, run_limits
from trailer_director.constraints import ConstraintEngine, ReasonCode
from trailer_director.constraints.catalog import RULE_CATALOG
from trailer_director.constraints.rules import DEFAULT_CLIP_RULES, DEFAULT_TRAILER_RULES
from trailer_director.data import validate_dataset
from trailer_director.domain import AudienceType
from trailer_director.llm.context import planning_context
from trailer_director.planning import (
    IssueCode,
    MockPlanner,
    ReplayPlanner,
    build_audience_strategy,
    build_evidence_pool,
    normalize_planner_output,
)
from trailer_director.planning.claims import check_hook, hook_sources
from trailer_director.planning.pipeline import campaign_context
from trailer_director.planning.replay import load_replay
from trailer_director.repair import DeterministicRepairPlanner, LoopStatus, OutagePlanner, RepairLoop, Verdict
from trailer_director.story import build_story_map

LIVE_DIR = DATA_DIR.parent / "runs"
FAMILY_FIXTURE = load_replay(REPLAY_DIR / "family.json").response


def _proposal_with_hook(evidence, hook, audience=AudienceType.FAMILY):
    payload = {**json.loads(json.dumps(FAMILY_FIXTURE)), "hook": hook, "audience": audience.value}
    proposal, issues = normalize_planner_output(payload, evidence, audience)
    assert proposal is not None, issues
    return proposal


class TestHookTruth:
    def _issues(self, evidence, engine, hook, audience=AudienceType.FAMILY):
        pool = build_evidence_pool(engine, evidence, campaign_context(evidence, audience))
        return [(i.severity, i.code) for i in check_hook(_proposal_with_hook(evidence, hook, audience), evidence, pool)]

    def test_hook_quoting_a_line_in_the_cut_passes(self, evidence, engine):
        assert self._issues(evidence, engine, "They have rotis, Maa. They just don't have you shouting at them.") == []

    def test_invented_clickbait_hook_is_an_error(self, evidence, engine):
        assert self._issues(evidence, engine, "Arjun turns on his own family!") == [
            ("error", IssueCode.HOOK_NOT_IN_EVIDENCE)
        ]

    def test_hook_quoting_a_spoiler_line_is_an_error(self, evidence, engine):
        reveal = evidence.dialogue_line("DLG_042").text  # the reveal, above the family ceiling

        assert set(self._issues(evidence, engine, reveal)) == {("error", IssueCode.HOOK_USES_INELIGIBLE_LINE)}

    def test_real_line_outside_the_cut_is_only_a_warning(self, evidence, engine):
        other = evidence.dialogue_line("DLG_014").text  # eligible for family, not in the fixture's clips

        assert set(self._issues(evidence, engine, other)) == {("warning", IssueCode.HOOK_NOT_IN_TRAILER)}

    @pytest.mark.parametrize("path", sorted([*REPLAY_DIR.glob("*.json"), *(LIVE_DIR / "planner_runs").glob("*.json")]))
    def test_every_recorded_hook_is_traceable(self, evidence, path):
        assert hook_sources(load_replay(path).response["hook"], evidence)

    def test_planner_hook_that_is_not_evidence_is_never_accepted(self, evidence, engine):
        payload = {**json.loads(json.dumps(FAMILY_FIXTURE)), "hook": "Arjun turns on his own family!"}

        class Clickbait:
            mode, version = "mock", "clickbait"

            def plan(self, *_):
                from trailer_director.planning import PlannerResponse

                return PlannerResponse(mode="mock", planner_version="clickbait", payload=payload)

        loop = RepairLoop(evidence, engine, DeterministicRepairPlanner(evidence))
        run = loop.run(Clickbait(), campaign_context(evidence, AudienceType.FAMILY), FIXED_NOW)

        assert run.attempts[0].status == "invalid_output"
        assert run.final_proposal.hook != payload["hook"]


class TestRelationshipTruth:
    def _codes(self, evidence, text, scene_clip):
        line_id = scene_clip.dialogue_ids[0]
        line = evidence.dialogue_line(line_id)
        changed = replace(evidence, dialogue={**evidence.dialogue, line_id: line.model_copy(update={"text": text})})
        result = ConstraintEngine(changed).evaluate_clip(scene_clip, context("dialect_region", territory="IN-HR"))
        return [
            (v.reason_code, v.entity_id) for v in result.errors if v.reason_code is ReasonCode.MISLEADING_RELATIONSHIP
        ]

    def test_honorific_subtitled_as_kinship_is_rejected(self, evidence):
        bus_stop = clip("SC01", "00:00:29.500", "00:00:33.500", ["DLG_004"])

        assert self._codes(evidence, "Some things can't be written down, Uncle.", bus_stop) == [
            (ReasonCode.MISLEADING_RELATIONSHIP, "DLG_004")
        ]

    def test_named_addressee_is_checked(self, evidence):
        bus_stop = clip("SC01", "00:00:29.500", "00:00:33.500", ["DLG_004"])

        assert self._codes(evidence, "Your eyes are fine, Bansi Uncle.", bus_stop)

    def test_true_kinship_is_allowed(self, evidence):
        # Meera addressing Raghav, her paternal uncle, in the storeroom (SC08).
        storeroom = clip("SC08", "00:07:54.500", "00:07:59.500", ["DLG_031"])

        assert self._codes(evidence, "Looking for something, Uncle?", storeroom) == []

    @pytest.mark.parametrize("audience", list(AudienceType))
    def test_shipped_dialogue_raises_no_relationship_errors(self, evidence, engine, audience):
        ctx = campaign_context(evidence, audience)
        flagged = [
            d
            for d in evidence.dialogue
            if ReasonCode.MISLEADING_RELATIONSHIP in engine.evaluate_dialogue(d, ctx).reason_codes
        ]

        assert flagged == []


class TestAudienceBias:
    def _biased(self, raw):
        profile = record(raw["audience_profiles"], "audience", "dialect_region")
        profile["preferred_tones"].append("funny village accents")
        profile["positioning_notes"] += " Play up the rustic comedy of the regional accents."
        return validate_dataset(raw)

    def test_stereotyping_preferences_are_withheld_and_flagged(self, raw):
        package, report = self._biased(raw)
        strategy = build_audience_strategy(package, AudienceType.DIALECT_REGION)

        assert "funny village accents" not in strategy.preferred_tones
        assert "rustic comedy" not in strategy.positioning
        assert len(strategy.withheld) == 2
        flagged = {w.location for w in report.warnings if "stereotyping" in w.message}
        assert flagged == {
            "audience_profiles.dialect_region.preferred_tones",
            "audience_profiles.dialect_region.positioning_notes",
        }

    def test_withheld_preferences_never_reach_the_model(self, raw):
        package, _ = self._biased(raw)
        engine = ConstraintEngine(package)
        ctx = campaign_context(package, AudienceType.DIALECT_REGION)
        strategy = build_audience_strategy(package, AudienceType.DIALECT_REGION)

        sent = json.dumps(
            planning_context(build_story_map(package), strategy, build_evidence_pool(engine, package, ctx), package)
        )

        assert "funny village accents" not in sent and "rustic comedy" not in sent

    def test_shipped_profiles_and_avoid_lists_are_not_flagged(self, evidence):
        for audience in AudienceType:
            assert build_audience_strategy(evidence, audience).withheld == []


class TestVerdict:
    def test_clean_accepted_plan_passes(self, evidence, engine):
        run = RepairLoop(evidence, engine, DeterministicRepairPlanner(evidence)).run(
            MockPlanner(evidence), campaign_context(evidence, AudienceType.FAMILY), FIXED_NOW
        )

        assert (run.status, run.verdict) == (LoopStatus.ACCEPTED, Verdict.PASS)

    def test_accepted_plan_with_review_findings_passes_with_warnings(self, evidence, engine):
        run = RepairLoop(evidence, engine, DeterministicRepairPlanner(evidence)).run(
            MockPlanner(evidence), campaign_context(evidence, AudienceType.DIALECT_REGION), FIXED_NOW
        )

        assert run.verdict is Verdict.PASS_WITH_WARNINGS

    def test_anything_not_accepted_is_rejected(self, evidence, engine):
        loop = RepairLoop(
            evidence, engine, DeterministicRepairPlanner(evidence), allow_fallback=False, sleep=lambda _: None
        )

        run = loop.run(
            OutagePlanner(MockPlanner(evidence), 5), campaign_context(evidence, AudienceType.FAMILY), FIXED_NOW
        )

        assert (run.status, run.verdict, run.final_proposal) == (LoopStatus.PLANNER_UNAVAILABLE, Verdict.REJECTED, None)


class TestRunLimits:
    def test_limits_default_to_the_cost_sheet(self, evidence):
        limits = run_limits(evidence.cost_sheet, environ={})

        assert (limits.max_model_calls, limits.max_cost, limits.allow_fallback) == (24, 1.5, True)

    def test_limits_can_tighten_from_flags_or_environment(self, evidence):
        env = {"TRAILER_DIRECTOR_MAX_MODEL_CALLS": "3", "TRAILER_DIRECTOR_MAX_COST": "0.1"}

        assert run_limits(evidence.cost_sheet, environ=env).max_model_calls == 3
        assert run_limits(evidence.cost_sheet, 2, 0.05, environ=env).max_cost == 0.05

    @pytest.mark.parametrize(
        ("calls", "cost", "env", "fragment"),
        [
            (99, None, {}, "between 0 and the cost sheet's 24"),
            (None, 2.0, {}, "between 0 and the cost sheet's 1.5"),
            (None, None, {"TRAILER_DIRECTOR_MAX_MODEL_CALLS": "many"}, "is not a valid int"),
        ],
    )
    def test_invalid_limits_fail_before_any_call(self, evidence, calls, cost, env, fragment):
        with pytest.raises(ConfigError, match=fragment):
            run_limits(evidence.cost_sheet, calls, cost, environ=env)

    def test_tightened_cost_limit_stops_a_paid_repair(self, capsys):
        args = [
            "repair",
            "--audience",
            "young_adult",
            "--mode",
            "replay",
            "--repair-mode",
            "replay",
            "--max-cost",
            "0.05",
        ]

        code = main([*args, "--data-dir", str(DATA_DIR), "--json"])

        run = json.loads(capsys.readouterr().out)
        assert code == 1
        assert (run["status"], run["verdict"], run["budget"]["max_estimated_cost"]) == (
            "budget_exhausted",
            "REJECTED",
            0.05,
        )

    def test_limit_above_the_cost_sheet_is_a_configuration_error(self, capsys):
        code = main(["repair", "--audience", "family", "--max-model-calls", "99", "--data-dir", str(DATA_DIR)])

        assert code == 2
        assert "cost sheet's 24" in capsys.readouterr().err


def test_constraint_catalogue_matches_the_engine():
    registered = {rule.rule_id for rule in (*DEFAULT_CLIP_RULES, *DEFAULT_TRAILER_RULES)}
    registered |= {"SOURCE_DIALOGUE_001", "SOURCE_MUSIC_001", "RIGHTS_ACTOR_RESTRICTION_001"}  # emitted by shared rules

    assert {r.rule_id for r in RULE_CATALOG} == registered
    codes = {code for r in RULE_CATALOG for code in r.reason_codes}
    assert codes == {code.value for code in ReasonCode}


class TestArtifacts:
    def test_three_distinct_verified_trailers_with_full_segments(self, evidence):
        trailers = [build_trailer(evidence, audience, LIVE_DIR) for audience in AudienceType]

        assert [t.edl["verdict"] for t in trailers] == ["PASS", "PASS", "PASS_WITH_WARNINGS"]
        assert len({tuple(s["scene_id"] for s in t.edl["segments"]) for t in trailers}) == 3
        for t in trailers:
            assert t.edl["hook"]["source_dialogue_ids"]
            assert {a["role"] for a in t.edl["human_approvals"]} >= {"editorial", "legal", "marketing"}
            for segment in t.edl["segments"]:
                assert {
                    "source_in",
                    "source_out",
                    "video",
                    "audio",
                    "subtitles",
                    "reason",
                    "evidence",
                    "risk_flags",
                } <= set(segment)

    def test_recorded_live_plans_are_used_where_they_exist(self, evidence, tmp_path):
        (tmp_path / "planner_runs").mkdir()
        recording = LIVE_DIR / "planner_runs" / "family.json"
        (tmp_path / "planner_runs" / "family.json").write_bytes(recording.read_bytes())

        family = build_trailer(evidence, AudienceType.FAMILY, tmp_path).edl
        dialect = build_trailer(evidence, AudienceType.DIALECT_REGION, tmp_path).edl

        assert family["planner"]["version"] == "llm-planner/claude-opus-5-5/high"
        assert dialect["planner"]["source"].startswith("deterministic mock planner")

    def test_export_is_offline_and_reproducible(self, evidence, tmp_path, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("network access attempted")

        for name in ("socket", "create_connection", "getaddrinfo"):
            monkeypatch.setattr(socket, name, refuse)

        first = write_artifacts(tmp_path / "a", evidence, LIVE_DIR, None)
        second = write_artifacts(tmp_path / "b", evidence, LIVE_DIR, None)

        assert [p.name for p in first] == [
            "story_map.json",
            "spoiler_map.json",
            "constraint_map.json",
            "family_trailer.json",
            "young_adult_trailer.json",
            "dialect_region_trailer.json",
            "validation_report.md",
        ]
        assert all(a.read_bytes() == b.read_bytes() for a, b in zip(first, second, strict=True))

    def test_spoiler_map_blocks_the_reveal_for_every_audience(self, evidence, tmp_path):
        write_artifacts(tmp_path, evidence, None, None)
        spoilers = json.loads((tmp_path / "spoiler_map.json").read_text(encoding="utf-8"))

        reveal = next(s for s in spoilers["scenes"] if s["scene_id"] == "SC10")
        assert reveal["protected_reveal"] is True
        assert set(reveal["audiences"].values()) == {"blocked (SPOILER_LEVEL_EXCEEDED)"}

    def test_replay_of_the_live_young_adult_plan_is_unchanged(self, evidence, engine):
        planner = ReplayPlanner(LIVE_DIR / "planner_runs")
        loop = RepairLoop(evidence, engine, DeterministicRepairPlanner(evidence))

        run = loop.run(planner, campaign_context(evidence, AudienceType.YOUNG_ADULT), FIXED_NOW)

        assert (run.status, run.verdict, run.budget.repair_attempts, run.fallbacks) == ("accepted", "PASS", 0, [])
        assert [c.scene_id for c in run.final_proposal.candidate.clips] == ["SC02", "SC03", "SC05", "SC05"]
        assert (run.budget.input_tokens, run.budget.output_tokens) == (9056, 3012)
        assert run.evidence_fingerprint == "f855637bbf6a9b74"
