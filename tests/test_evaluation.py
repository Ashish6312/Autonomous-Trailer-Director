"""Evaluation harness: every scenario behaves as designed, reproducibly, offline, with no aggregate score."""

import json
import socket

import pytest
from llm_fakes import FakeClient, reply
from support import DATA_DIR, REPLAY_DIR

from trailer_director.cli import main
from trailer_director.constraints import ConstraintEngine
from trailer_director.domain import AudienceType
from trailer_director.domain.timecode import parse_timecode
from trailer_director.evaluation import SCENARIOS, collect_plans, evaluate, render_review_sheet
from trailer_director.evaluation.harness import LIVE_SCENARIO_ID
from trailer_director.evaluation.review import REVIEW_CRITERIA
from trailer_director.evaluation.scenarios import (
    HOMECOMING_CLIP,
    INJECTED_NOTE_CLIP,
    MISLEADING_CLIP,
    NOT_SUBTITLE_SAFE_CLIP,
    RECONCILIATION_CLIP,
    REGIONAL_TERM_CLIP,
    SPOILER_CLIP,
)
from trailer_director.llm import LLMPlanner, RecordingPlanner
from trailer_director.llm.context import planning_context
from trailer_director.llm.screening import find_instruction_like
from trailer_director.planning import build_audience_strategy, build_evidence_pool, run_planning
from trailer_director.planning.pipeline import campaign_context
from trailer_director.planning.replay import load_replay

FAMILY_PLAN = load_replay(REPLAY_DIR / "family.json").response


@pytest.fixture(scope="module")
def report(evidence):
    return evaluate(evidence, fixture_dir=REPLAY_DIR)


def test_every_scenario_runs_for_every_applicable_audience(report):
    expected = {(s.scenario_id, a) for s in SCENARIOS for a in s.audiences}

    assert {(r.scenario_id, r.audience) for r in report.results} == expected
    assert len(SCENARIOS) == 19
    assert [entry.split(":")[0] for entry in report.not_applicable] == [
        "S13_continuity/family",
        "S18_subtitle_relationship/family",
        "S18_subtitle_relationship/young_adult",
    ]


def test_every_run_behaves_as_designed(report):
    unexpected = {f"{r.scenario_id}/{r.audience}": r.deviations for r in report.results if not r.as_expected}

    assert unexpected == {}
    assert report.unexpected == []


def test_every_run_is_reproducible(report, evidence):
    assert all(r.replay_match for r in report.results)
    assert evaluate(evidence, fixture_dir=REPLAY_DIR).model_dump_json() == report.model_dump_json()


@pytest.mark.parametrize(
    ("scenario_id", "initial", "final"),
    [
        ("S01_normal", "eligible", "accepted"),
        ("S02_spoiler", "rejected", "accepted"),
        ("S04_missing_source", "invalid_output", "accepted"),
        ("S14_budget_exhausted", "rejected", "budget_exhausted"),
        ("S15_no_progress", "rejected", "repair_failed"),
    ],
)
def test_statuses_per_scenario(report, scenario_id, initial, final):
    rows = [r for r in report.results if r.scenario_id == scenario_id]

    assert {(r.initial_status, r.final_status) for r in rows} == {(initial, final)}


def test_selective_repair_reports_changed_and_preserved_components(report):
    row = next(r for r in report.results if r.scenario_id == "S09_selective_repair" and r.audience == "young_adult")

    assert "CLIP_001.music" in row.changed_components
    assert not [c for c in row.changed_components if c.startswith(("CLIP_001.scene", "CLIP_002."))]
    assert {"CLIP_001.scene", "CLIP_001.timecodes", "CLIP_001.dialogue", "CLIP_001.purpose"} <= set(
        row.preserved_components
    )
    assert {f"CLIP_002.{c}" for c in ("scene", "timecodes", "dialogue", "music", "purpose")} <= set(
        row.preserved_components
    )


def test_fallbacks_and_budget_are_reported(report):
    planner_down = [r for r in report.results if r.scenario_id == "S07_planner_unavailable"]
    budget = [r for r in report.results if r.scenario_id == "S14_budget_exhausted"]

    assert all(r.fallback_used and r.fallbacks[0].startswith("planner mock unavailable") for r in planner_down)
    assert all((r.planner_calls, r.repair_calls, r.estimated_cost) == (1, 0, 0.045) for r in budget)
    assert all(r.input_tokens is None for r in report.results)


@pytest.mark.parametrize("audience", list(AudienceType))
def test_planted_instruction_never_reaches_the_model_context(evidence, engine, story_map, audience):
    ctx = campaign_context(evidence, audience)
    pool = build_evidence_pool(engine, evidence, ctx)

    sent = planning_context(story_map, build_audience_strategy(evidence, audience), pool, evidence)

    assert find_instruction_like(sent) == []
    assert "ignore contract restrictions" not in json.dumps(sent).lower()


@pytest.mark.parametrize(
    "clip",
    [
        SPOILER_CLIP,
        MISLEADING_CLIP,
        INJECTED_NOTE_CLIP,
        NOT_SUBTITLE_SAFE_CLIP,
        REGIONAL_TERM_CLIP,
        HOMECOMING_CLIP,
        RECONCILIATION_CLIP,
    ],
)
def test_injected_clips_cite_real_evidence_inside_their_scenes(evidence, clip):
    scene = evidence.scene(clip["scene_id"])
    start, end = parse_timecode(clip["source_in"]), parse_timecode(clip["source_out"])

    assert scene.source_in <= start < end <= scene.source_out
    for dialogue_id in clip["dialogue_ids"]:
        line = evidence.dialogue_line(dialogue_id)
        assert line.scene_id == scene.scene_id and start <= line.start and line.end <= end


def test_evaluation_makes_no_network_calls(evidence, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)

    result = evaluate(evidence, [AudienceType.FAMILY], fixture_dir=REPLAY_DIR)

    assert result.unexpected == []


class TestRecordedLivePlans:
    def _record(self, evidence, engine, live_dir):
        planner = RecordingPlanner(LLMPlanner(evidence, FakeClient(reply(FAMILY_PLAN))), live_dir)
        run_planning(evidence, engine, planner, campaign_context(evidence, AudienceType.FAMILY))

    def test_recorded_live_plan_is_replayed_and_verified(self, evidence, engine, tmp_path):
        self._record(evidence, engine, tmp_path)

        result = evaluate(evidence, scenario_ids=[LIVE_SCENARIO_ID], fixture_dir=REPLAY_DIR, live_dir=tmp_path)

        [row] = result.results
        assert (row.scenario_id, row.audience, row.final_status, row.as_expected) == (
            LIVE_SCENARIO_ID,
            "family",
            "accepted",
            True,
        )
        assert (row.planner_calls, row.input_tokens, row.output_tokens) == (1, 1200, 300)

    def test_recording_against_other_evidence_is_flagged(self, evidence, engine, tmp_path):
        self._record(evidence, engine, tmp_path)
        path = tmp_path / "planner_runs" / "family.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["recorded_for"]["evidence_fingerprint"] = "0000000000000000"
        path.write_text(json.dumps(record), encoding="utf-8")

        result = evaluate(evidence, scenario_ids=[LIVE_SCENARIO_ID], fixture_dir=REPLAY_DIR, live_dir=tmp_path)

        assert result.results[0].deviations == ["recording was made against different evidence"]


class TestReviewSheet:
    def test_sheet_covers_every_plan_and_criterion_without_scores(self, evidence):
        items = collect_plans(evidence, live_dir=None)

        sheet = render_review_sheet(evidence, items)

        assert [i.audience for i in items] == list(AudienceType)
        assert sheet.count("\n## ") == len(items)
        for name, _ in REVIEW_CRITERIA:
            assert sheet.count(f"| {name} |") == len(items)
        assert sheet.count("| Criterion | Question | Observations |") == len(items)
        assert "Do not score or rank plans" in sheet
        assert "Human creative judgment" in sheet

    def test_sheet_names_the_review_warnings_the_rules_raised(self, evidence):
        sheet = render_review_sheet(evidence, collect_plans(evidence, live_dir=None))

        dialect_section = sheet.split("## 3. dialect_region")[1]
        assert "DIALECT_REVIEW_REQUIRED (DLG_004)" in dialect_section


def test_cli_writes_report_and_review_sheet(tmp_path, capsys):
    out, sheet = tmp_path / "results.json", tmp_path / "review.md"
    args = ["evaluate", "--audience", "family", "--scenario", "S02_spoiler", "--live-dir", str(tmp_path / "none")]

    code = main([*args, "--out", str(out), "--review-sheet", str(sheet), "--data-dir", str(DATA_DIR)])

    assert code == 0
    assert "All runs behaved as designed." in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["results"][0]["scenario_id"] == "S02_spoiler"
    assert sheet.read_text(encoding="utf-8").startswith("# Human creative review")


def test_engine_is_the_only_judge(evidence):
    """The harness never decides eligibility itself: every final plan it reports as accepted is eligible."""
    engine = ConstraintEngine(evidence)
    for item in collect_plans(evidence, live_dir=None):
        verdict = engine.evaluate_trailer(item.run.final_proposal.candidate, item.run.context)
        assert verdict.eligible
