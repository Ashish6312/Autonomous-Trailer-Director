"""End-to-end planning runs: planner -> normaliser -> constraint engine, in mock and replay mode."""

import json
import shutil
import socket

import pytest
from support import DATA_DIR, FIXED_NOW, REPLAY_DIR

from trailer_director.cli import EXIT_INVALID, EXIT_OK, main
from trailer_director.constraints import ReasonCode
from trailer_director.domain import AudienceType
from trailer_director.errors import PlannerError
from trailer_director.planning import (
    IssueCode,
    MockPlanner,
    PlannerMode,
    PlannerResponse,
    ReplayPlanner,
    RunStatus,
    run_planning,
)
from trailer_director.planning.pipeline import campaign_context


class StubPlanner:
    """Returns a fixed payload, standing in for a planner that ignores the rules."""

    mode = PlannerMode.REPLAY
    version = "stub"

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def plan(self, story_map, strategy, pool):
        if self._error:
            raise self._error
        return PlannerResponse(mode=self.mode, planner_version=self.version, payload=self._payload)


def _run(evidence, engine, planner, audience=AudienceType.FAMILY, **context_overrides):
    return run_planning(evidence, engine, planner, campaign_context(evidence, audience, **context_overrides), FIXED_NOW)


def _payload(scene_id, source_in, source_out, dialogue_ids, audience="family"):
    return {
        "audience": audience,
        "trailer_id": "TRL_STUB",
        "title": "Stub",
        "hook": "They have rotis, Maa.",  # a real line (DLG_012): the hook must quote the evidence
        "positioning": "Stub positioning",
        "rationale": "Stub rationale",
        "clips": [
            {
                "clip_id": "CLIP_001",
                "scene_id": scene_id,
                "source_in": source_in,
                "source_out": source_out,
                "dialogue_ids": dialogue_ids,
                "purpose": "stub",
                "reason": f"Claims {scene_id} is approved for every audience.",
                "evidence": [scene_id, *dialogue_ids],
            }
        ],
    }


@pytest.mark.parametrize("audience", list(AudienceType))
def test_mock_plans_reach_the_engine_and_pass(evidence, engine, audience):
    outcome = _run(evidence, engine, MockPlanner(evidence), audience)
    run = outcome.run

    assert run.status is RunStatus.ELIGIBLE
    assert run.eligibility == engine.evaluate_trailer(run.proposal.candidate, run.context)


def test_engine_rejects_a_plan_the_planner_calls_approved(evidence, engine):
    payload = _payload("SC10", "00:11:14.500", "00:11:19.500", ["DLG_042"])

    run = _run(evidence, engine, StubPlanner(payload)).run

    assert run.status is RunStatus.REJECTED
    assert (ReasonCode.SPOILER_LEVEL_EXCEEDED, "SC10") in {(v.reason_code, v.entity_id) for v in run.eligibility.errors}


def test_invalid_output_stops_before_the_engine(evidence, engine):
    run = _run(evidence, engine, StubPlanner(_payload("SC99", "00:00:01.000", "00:00:02.000", []))).run

    assert run.status is RunStatus.INVALID_OUTPUT
    assert run.eligibility is None and run.proposal is None
    assert IssueCode.UNKNOWN_SCENE in {issue.code for issue in run.issues}


def test_planner_failure_is_recorded_not_raised(evidence, engine):
    run = _run(evidence, engine, StubPlanner(error=PlannerError("provider unavailable"))).run

    assert run.status is RunStatus.PLANNER_FAILED
    assert [(i.code, i.message) for i in run.issues] == [(IssueCode.PLANNER_FAILED, "provider unavailable")]


def test_run_record_is_traceable(evidence, engine):
    run = _run(evidence, engine, ReplayPlanner(REPLAY_DIR)).run

    assert run.episode_id == evidence.episode.episode_id
    assert run.evidence_version == evidence.episode.version
    assert run.evidence_fingerprint == evidence.fingerprint()
    assert (run.mode, run.planner_version) == (PlannerMode.REPLAY, "recorded-planner-fixture-0.1")
    assert run.created_at == FIXED_NOW
    assert run.model_calls == 1
    assert run.estimated_model_cost == pytest.approx(0.045)
    assert run.estimated_duration_seconds == run.proposal.candidate.duration_ms / 1000


@pytest.mark.parametrize(
    ("audience", "status"),
    [
        (AudienceType.FAMILY, RunStatus.ELIGIBLE),
        (AudienceType.YOUNG_ADULT, RunStatus.REJECTED),
        (AudienceType.DIALECT_REGION, RunStatus.ELIGIBLE),
    ],
)
def test_replays_give_recorded_outcomes(evidence, engine, audience, status):
    run = _run(evidence, engine, ReplayPlanner(REPLAY_DIR), audience).run

    assert run.status is status
    assert IssueCode.REPLAY_EVIDENCE_MISMATCH not in {issue.code for issue in run.issues}


def test_young_adult_replay_is_rejected_for_the_reveal_clip(evidence, engine):
    run = _run(evidence, engine, ReplayPlanner(REPLAY_DIR), AudienceType.YOUNG_ADULT).run

    assert {(v.clip_id, v.entity_id) for v in run.eligibility.errors} == {("CLIP_003", "SC10"), ("CLIP_003", "DLG_042")}


@pytest.mark.parametrize("mode", ["mock", "replay"])
def test_same_input_gives_same_run(evidence, engine, mode):
    def planner():
        return MockPlanner(evidence) if mode == "mock" else ReplayPlanner(REPLAY_DIR)

    first = _run(evidence, engine, planner()).run
    second = _run(evidence, engine, planner()).run

    assert first.model_dump_json() == second.model_dump_json()
    assert first.run_id.startswith("RUN_")


def test_replay_against_changed_evidence_is_flagged(evidence, engine, tmp_path):
    record = json.loads((REPLAY_DIR / "family.json").read_text(encoding="utf-8"))
    record["recorded_for"]["evidence_fingerprint"] = "0000000000000000"
    (tmp_path / "family.json").write_text(json.dumps(record), encoding="utf-8")

    run = _run(evidence, engine, ReplayPlanner(tmp_path)).run

    assert IssueCode.REPLAY_EVIDENCE_MISMATCH in {issue.code for issue in run.issues}
    assert run.status is RunStatus.ELIGIBLE


@pytest.mark.parametrize("content", [None, "{not json", json.dumps({"format_version": 2})])
def test_missing_or_broken_replay_fails_cleanly(evidence, engine, tmp_path, content):
    if content is not None:
        (tmp_path / "family.json").write_text(content, encoding="utf-8")

    run = _run(evidence, engine, ReplayPlanner(tmp_path)).run

    assert run.status is RunStatus.PLANNER_FAILED


def test_planning_makes_no_network_calls(evidence, engine, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    for audience in AudienceType:
        assert _run(evidence, engine, MockPlanner(evidence), audience).run.status is RunStatus.ELIGIBLE
        assert _run(evidence, engine, ReplayPlanner(REPLAY_DIR), audience).run.status is not RunStatus.PLANNER_FAILED


def test_cli_plan_mock(capsys):
    code = main(["plan", "--audience", "family", "--mode", "mock", "--data-dir", str(DATA_DIR)])

    output = capsys.readouterr().out
    assert code == EXIT_OK
    for heading in (
        "Story map generated",
        "Audience strategy generated",
        "Eligible evidence",
        "Planner mode: mock",
        "Candidate selected",
        "Constraint result",
    ):
        assert heading in output


def test_cli_plan_replay_json(capsys, tmp_path):
    replay_dir = tmp_path / "runs"
    shutil.copytree(REPLAY_DIR, replay_dir)
    args = ["plan", "--audience", "young_adult", "--mode", "replay", "--replay-dir", str(replay_dir)]

    code = main([*args, "--data-dir", str(DATA_DIR), "--json"])

    run = json.loads(capsys.readouterr().out)
    assert code == EXIT_INVALID
    assert run["status"] == "rejected"
    assert run["mode"] == "replay"


def test_cli_plan_uses_regional_territory_for_dialect_audience(capsys):
    code = main(["plan", "--audience", "dialect_region", "--data-dir", str(DATA_DIR), "--json"])

    run = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert run["context"]["territory"] == "IN-HR"


@pytest.mark.parametrize("audience", ["dialect_region", AudienceType.DIALECT_REGION])
def test_campaign_context_gives_the_regional_territory_for_a_plain_string_too(evidence, audience):
    # Regression: an identity check against the enum gave a plain string the country default (IN), which
    # the region-only performer licence (ACT_05) then rejects.
    assert campaign_context(evidence, audience).territory == "IN-HR"


def test_campaign_context_rejects_an_unknown_audience(evidence):
    with pytest.raises(ValueError):
        campaign_context(evidence, "teenagers")
