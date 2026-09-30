"""The verify -> repair -> re-verify loop, retry/fallback, budget and selective replanning."""

import json
import socket
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from support import FIXED_NOW, REPLAY_DIR

from trailer_director.constraints import ReasonCode
from trailer_director.domain import AudienceType
from trailer_director.errors import PlannerError
from trailer_director.planning import IssueCode, MockPlanner, PlannerMode, PlannerResponse, ReplayPlanner, RunStatus
from trailer_director.planning.pipeline import campaign_context
from trailer_director.planning.replay import load_replay
from trailer_director.repair import (
    AttemptKind,
    ClipAction,
    DeterministicRepairPlanner,
    LoopStatus,
    OutagePlanner,
    RepairLoop,
    ReplayRepairPlanner,
    VerificationCode,
)
from trailer_director.repair.planners import load_repair_replay

REPAIR_REPLAY_DIR = Path(__file__).resolve().parents[1] / "examples" / "repair_runs"
YOUNG_ADULT_REPLAY = load_replay(REPLAY_DIR / "young_adult.json").response


class StubRepairer:
    """Returns scripted payloads per attempt (or raises), standing in for an unreliable model."""

    mode = PlannerMode.REPLAY
    version = "stub-repair"

    def __init__(self, *payloads):
        self._payloads = payloads
        self.requests = []

    def repair(self, request):
        self.requests.append(request)
        payload = self._payloads[min(request.attempt, len(self._payloads)) - 1]
        if isinstance(payload, Exception):
            raise payload
        return PlannerResponse(mode=self.mode, planner_version=self.version, payload=payload, model_calls=1)


def _young_adult_with(clip_3_overrides):
    payload = json.loads(json.dumps(YOUNG_ADULT_REPLAY))
    payload["clips"][2].update(clip_3_overrides)
    return payload


def _loop(evidence, engine, repairer=None, **kwargs):
    kwargs.setdefault("sleep", lambda seconds: None)
    return RepairLoop(evidence, engine, repairer or DeterministicRepairPlanner(evidence), **kwargs)


def _ctx(evidence, audience=AudienceType.YOUNG_ADULT, on=None):
    return campaign_context(evidence, audience, date.fromisoformat(on) if on else None)


def test_rejected_plan_is_repaired_selectively(evidence, engine):
    run = _loop(evidence, engine).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    initial, repair = run.attempts
    assert run.status is LoopStatus.ACCEPTED
    assert (initial.status, repair.status) == (RunStatus.REJECTED, RunStatus.ELIGIBLE)
    assert {c.clip_id: c.action for c in repair.changes} == {
        "CLIP_001": ClipAction.KEPT,
        "CLIP_002": ClipAction.KEPT,
        "CLIP_003": ClipAction.REPLACED,
    }
    kept_before = {c.clip_id: c for c in initial.proposal.candidate.clips if c.clip_id != "CLIP_003"}
    kept_after = {c.clip_id: c for c in run.final_proposal.candidate.clips if c.clip_id != "CLIP_003"}
    assert kept_after == kept_before
    assert "SC10" not in {c.scene_id for c in run.final_proposal.candidate.clips}


def test_engine_remains_the_judge_of_the_repaired_plan(evidence, engine):
    run = _loop(evidence, engine).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    assert run.final_eligibility == engine.evaluate_trailer(run.final_proposal.candidate, run.context)
    assert run.final_eligibility.eligible


def test_fully_replayed_repair_is_charged_from_the_cost_sheet(evidence, engine):
    loop = _loop(evidence, engine, ReplayRepairPlanner(REPAIR_REPLAY_DIR))

    run = loop.run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    assert run.status is LoopStatus.ACCEPTED
    assert (run.budget.model_calls, run.budget.estimated_cost) == (2, pytest.approx(0.065))
    assert [a.planner_version for a in run.attempts] == ["recorded-planner-fixture-0.1", "recorded-repair-fixture-0.1"]


def test_eligible_plan_needs_no_repair(evidence, engine):
    run = _loop(evidence, engine).run(MockPlanner(evidence), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW)

    assert run.status is LoopStatus.ACCEPTED
    assert [a.kind for a in run.attempts] == [AttemptKind.INITIAL]
    assert run.budget.repair_attempts == 0


class TestRightsChangeReplanning:
    def test_expired_music_is_swapped_and_footage_kept(self, evidence, engine):
        loop = _loop(evidence, engine)
        before = loop.run(MockPlanner(evidence), _ctx(evidence, on="2026-10-20"), FIXED_NOW)
        assert {c.music_id for c in before.final_proposal.candidate.clips} == {"MUS_03"}

        after = loop.replan(before.final_proposal, _ctx(evidence, on="2026-11-14"), FIXED_NOW)

        reverify, repair = after.attempts
        assert reverify.kind is AttemptKind.REVERIFY
        assert {d.reason_codes[0] for d in reverify.failing_clips} == {ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED}
        assert {c.action for c in repair.changes} == {ClipAction.MUSIC_SWAPPED}
        footage = [
            (c.scene_id, c.source_in, c.source_out, c.dialogue_ids) for c in before.final_proposal.candidate.clips
        ]
        assert footage == [
            (c.scene_id, c.source_in, c.source_out, c.dialogue_ids) for c in after.final_proposal.candidate.clips
        ]
        assert "MUS_03" not in {c.music_id for c in after.final_proposal.candidate.clips}
        assert after.status is LoopStatus.ACCEPTED

    def test_unchanged_conditions_need_no_repair(self, evidence, engine):
        loop = _loop(evidence, engine)
        plan = loop.run(MockPlanner(evidence), _ctx(evidence), FIXED_NOW).final_proposal

        run = loop.replan(plan, _ctx(evidence), FIXED_NOW)

        assert [a.kind for a in run.attempts] == [AttemptKind.REVERIFY]
        assert run.status is LoopStatus.ACCEPTED

    def test_plan_for_another_audience_is_refused(self, evidence, engine):
        loop = _loop(evidence, engine)
        plan = loop.run(MockPlanner(evidence), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW).final_proposal

        with pytest.raises(ValueError, match="family"):
            loop.replan(plan, _ctx(evidence), FIXED_NOW)


class TestRetryAndFallback:
    def test_transient_outage_is_retried_with_backoff(self, evidence, engine):
        sleeps = []
        loop = _loop(evidence, engine, sleep=sleeps.append)

        run = loop.run(OutagePlanner(MockPlanner(evidence), failures=2), _ctx(evidence), FIXED_NOW)

        assert [(a.kind, a.status) for a in run.attempts] == [
            (AttemptKind.INITIAL, RunStatus.PLANNER_FAILED),
            (AttemptKind.RETRY, RunStatus.PLANNER_FAILED),
            (AttemptKind.RETRY, RunStatus.ELIGIBLE),
        ]
        assert sleeps == [2.0, 4.0]
        assert run.status is LoopStatus.ACCEPTED

    def test_persistent_outage_falls_back_to_replay(self, evidence, engine):
        loop = _loop(evidence, engine, fallback_planner=ReplayPlanner(REPLAY_DIR))

        run = loop.run(
            OutagePlanner(MockPlanner(evidence), failures=10), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW
        )

        assert run.attempts[-1].kind is AttemptKind.FALLBACK
        assert run.attempts[-1].planner_mode == "replay"
        assert run.status is LoopStatus.ACCEPTED

    def test_without_fallback_the_run_reports_the_outage(self, evidence, engine):
        run = _loop(evidence, engine).run(OutagePlanner(MockPlanner(evidence), 10), _ctx(evidence), FIXED_NOW)

        assert run.status is LoopStatus.PLANNER_UNAVAILABLE
        assert run.final_proposal is None
        assert len(run.attempts) == 1 + evidence.cost_sheet.fallback_strategy.max_provider_retries

    def test_cost_sheet_can_forbid_the_fallback(self, evidence, engine):
        fallback = evidence.cost_sheet.fallback_strategy.model_copy(
            update={"on_provider_unavailable": "reject_with_report"}
        )
        strict = replace(evidence, cost_sheet=evidence.cost_sheet.model_copy(update={"fallback_strategy": fallback}))
        loop = _loop(strict, engine, fallback_planner=ReplayPlanner(REPLAY_DIR))

        run = loop.run(OutagePlanner(MockPlanner(strict), 10), _ctx(strict), FIXED_NOW)

        assert run.status is LoopStatus.PLANNER_UNAVAILABLE


def test_budget_exhaustion_stops_the_loop(evidence, engine):
    limits = evidence.cost_sheet.limits.model_copy(update={"max_model_calls": 1})
    tight = replace(evidence, cost_sheet=evidence.cost_sheet.model_copy(update={"limits": limits}))
    loop = _loop(tight, engine, ReplayRepairPlanner(REPAIR_REPLAY_DIR))

    run = loop.run(ReplayPlanner(REPLAY_DIR), _ctx(tight), FIXED_NOW)

    assert run.status is LoopStatus.BUDGET_EXHAUSTED
    assert run.final_proposal is None
    assert run.final_eligibility is not None and not run.final_eligibility.eligible
    assert run.budget.model_calls == 1


def test_attempts_run_out_when_every_repair_still_fails(evidence, engine):
    still_spoilers = [
        _young_adult_with({"source_in": s, "source_out": e, "dialogue_ids": [d], "evidence": ["SC10", d]})
        for s, e, d in [
            ("00:10:24.500", "00:10:30.500", "DLG_039"),
            ("00:11:24.500", "00:11:28.500", "DLG_043"),
            ("00:10:39.500", "00:10:48.500", "DLG_040"),
        ]
    ]
    run = _loop(evidence, engine, StubRepairer(*still_spoilers)).run(
        ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW
    )

    assert run.status is LoopStatus.ATTEMPTS_EXHAUSTED
    assert run.budget.repair_attempts == 3
    assert all(a.status is RunStatus.REJECTED for a in run.attempts)


def test_repair_that_touches_a_passing_clip_is_not_adopted(evidence, engine):
    tampered = json.loads(json.dumps(load_repair_replay(REPAIR_REPLAY_DIR / "young_adult.json").attempts[0].response))
    tampered["clips"][0]["music_id"] = "MUS_02"
    honest = load_repair_replay(REPAIR_REPLAY_DIR / "young_adult.json").attempts[0].response
    repairer = StubRepairer(tampered, honest)

    run = _loop(evidence, engine, repairer).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    first_repair = run.attempts[1]
    assert first_repair.status is RunStatus.INVALID_OUTPUT
    assert VerificationCode.KEPT_CLIP_CHANGED in {i.code for i in first_repair.verification_issues}
    assert repairer.requests[1].proposal == run.attempts[0].proposal
    assert run.status is LoopStatus.ACCEPTED


def test_malformed_repair_output_is_reported_and_the_loop_continues(evidence, engine):
    honest = load_repair_replay(REPAIR_REPLAY_DIR / "young_adult.json").attempts[0].response
    repairer = StubRepairer(_young_adult_with({"scene_id": "SC99"}), honest)

    run = _loop(evidence, engine, repairer).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    assert run.attempts[1].status is RunStatus.INVALID_OUTPUT
    assert IssueCode.UNKNOWN_SCENE in {i.code for i in run.attempts[1].planning_issues}
    assert run.status is LoopStatus.ACCEPTED


def test_repairer_failure_is_retried_then_reported(evidence, engine):
    repairer = StubRepairer(PlannerError("repair provider unavailable"))

    run = _loop(evidence, engine, repairer).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    assert run.status is LoopStatus.REPAIR_FAILED
    assert len(repairer.requests) == 1 + evidence.cost_sheet.fallback_strategy.max_provider_retries


def test_repair_without_progress_stops(evidence, engine):
    run = _loop(evidence, engine, StubRepairer(YOUNG_ADULT_REPLAY)).run(
        ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW
    )

    assert run.status is LoopStatus.REPAIR_FAILED
    assert VerificationCode.NO_PROGRESS in {i.code for i in run.attempts[-1].verification_issues}


def test_clip_is_dropped_when_no_replacement_fits(evidence, engine):
    profile = evidence.audience_profile(AudienceType.YOUNG_ADULT).model_copy(
        update={"target_trailer_duration_seconds": 31}
    )
    short = replace(evidence, audience_profiles={**evidence.audience_profiles, AudienceType.YOUNG_ADULT: profile})

    run = _loop(short, engine).run(ReplayPlanner(REPLAY_DIR), _ctx(short), FIXED_NOW)

    assert run.status is LoopStatus.ACCEPTED
    assert {c.clip_id: c.action for c in run.attempts[-1].changes}["CLIP_003"] is ClipAction.DROPPED
    assert [c.clip_id for c in run.final_proposal.candidate.clips] == ["CLIP_001", "CLIP_002"]


def test_repair_request_carries_exclusions(evidence, engine):
    repairer = StubRepairer(load_repair_replay(REPAIR_REPLAY_DIR / "young_adult.json").attempts[0].response)

    _loop(evidence, engine, repairer).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    [request] = repairer.requests
    assert [d.clip_id for d in request.failing_clips] == ["CLIP_003"]
    assert request.excluded_ids == ["SC10"]


@pytest.mark.parametrize("mode", ["mock", "replay"])
def test_repair_runs_are_deterministic(evidence, engine, mode):
    def run_once():
        repairer = DeterministicRepairPlanner(evidence) if mode == "mock" else ReplayRepairPlanner(REPAIR_REPLAY_DIR)
        return _loop(evidence, engine, repairer).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    first, second = run_once(), run_once()

    assert first.model_dump_json() == second.model_dump_json()
    assert first.run_id.startswith("REPAIR_")


def test_repair_loop_makes_no_network_calls(evidence, engine, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)

    for repairer in (DeterministicRepairPlanner(evidence), ReplayRepairPlanner(REPAIR_REPLAY_DIR)):
        run = _loop(evidence, engine, repairer).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)
        assert run.status is LoopStatus.ACCEPTED
