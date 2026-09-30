"""LLM planner and repairer behind the existing interfaces: untrusted output, same guards, same engine."""

import json
import socket
import sys
from dataclasses import replace

import pytest
from llm_fakes import FakeClient, connection_error, reply, status_error
from support import DATA_DIR, FIXED_NOW, REPLAY_DIR

from trailer_director.cli import main
from trailer_director.domain import AudienceType
from trailer_director.errors import PlannerError
from trailer_director.llm import LLMPlanner, LLMRepairPlanner, RecordingPlanner, RecordingRepairPlanner, create_client
from trailer_director.planning import (
    IssueCode,
    PlannerMode,
    PlannerResponse,
    ReplayPlanner,
    RunStatus,
    build_audience_strategy,
    build_evidence_pool,
    run_planning,
)
from trailer_director.planning.pipeline import campaign_context
from trailer_director.planning.replay import load_replay
from trailer_director.repair import (
    AttemptKind,
    DeterministicRepairPlanner,
    LoopStatus,
    RepairLoop,
    ReplayRepairPlanner,
    VerificationCode,
)
from trailer_director.repair.planners import load_repair_replay

REPAIR_DIR = REPLAY_DIR.parent / "repair_runs"
FAMILY_PLAN = load_replay(REPLAY_DIR / "family.json").response
SPOILER_PLAN = load_replay(REPLAY_DIR / "young_adult.json").response
HONEST_REPAIR = load_repair_replay(REPAIR_DIR / "young_adult.json").attempts[0].response


def _ctx(evidence, audience=AudienceType.YOUNG_ADULT):
    return campaign_context(evidence, audience)


def _loop(evidence, engine, repairer, sleeps=None, **kwargs):
    return RepairLoop(
        evidence, engine, repairer, sleep=(sleeps.append if sleeps is not None else lambda s: None), **kwargs
    )


def test_llm_plan_is_normalised_and_judged_like_any_other(evidence, engine):
    client = FakeClient(reply(FAMILY_PLAN, input_tokens=5000, output_tokens=800))

    run = run_planning(
        evidence, engine, LLMPlanner(evidence, client), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW
    ).run

    assert run.status is RunStatus.ELIGIBLE
    assert (run.mode, run.planner_version) == (PlannerMode.LLM, "llm-planner/claude-opus-5-5/high")
    assert run.model_calls == 1
    assert run.eligibility == engine.evaluate_trailer(run.proposal.candidate, run.context)


def test_engine_rejects_llm_spoiler_and_llm_repair_is_verified(evidence, engine):
    planner_client = FakeClient(reply(SPOILER_PLAN, input_tokens=6000, output_tokens=900))
    repair_client = FakeClient(reply(HONEST_REPAIR, input_tokens=7000, output_tokens=950))
    loop = _loop(evidence, engine, LLMRepairPlanner(evidence, repair_client))

    run = loop.run(LLMPlanner(evidence, planner_client), _ctx(evidence), FIXED_NOW)

    initial, repair = run.attempts
    assert (initial.planner_mode, initial.status) == ("llm", RunStatus.REJECTED)
    assert (repair.planner_mode, repair.status) == ("llm", RunStatus.ELIGIBLE)
    assert run.status is LoopStatus.ACCEPTED
    assert (run.budget.model_calls, run.budget.estimated_cost) == (2, pytest.approx(0.065))
    assert (run.budget.input_tokens, run.budget.output_tokens) == (13000, 1850)
    assert repair_client.context_of(0)["repair_decisions"][0]["clip_id"] == "CLIP_003"


def test_llm_repair_that_breaks_a_decision_is_rejected_then_retried(evidence, engine):
    tampered = json.loads(json.dumps(HONEST_REPAIR))
    tampered["clips"][0]["music_id"] = "MUS_02"
    repair_client = FakeClient(reply(tampered), reply(HONEST_REPAIR))
    loop = _loop(evidence, engine, LLMRepairPlanner(evidence, repair_client))

    run = loop.run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

    first_repair = run.attempts[1]
    assert first_repair.status is RunStatus.INVALID_OUTPUT
    assert VerificationCode.KEPT_CLIP_CHANGED in {i.code for i in first_repair.verification_issues}
    assert run.status is LoopStatus.ACCEPTED


def test_unparseable_llm_output_leads_to_a_fresh_plan_request(evidence, engine):
    planner_client = FakeClient(reply("Sure! Here is my plan: ..."))
    repair_client = FakeClient(reply(FAMILY_PLAN))
    loop = _loop(evidence, engine, LLMRepairPlanner(evidence, repair_client))

    run = loop.run(LLMPlanner(evidence, planner_client), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW)

    assert run.attempts[0].status is RunStatus.INVALID_OUTPUT
    assert IssueCode.MALFORMED_OUTPUT in {i.code for i in run.attempts[0].planning_issues}
    sent = repair_client.context_of(0)
    assert sent["current_plan"] is None and sent["previous_output_problems"]
    assert run.status is LoopStatus.ACCEPTED


@pytest.mark.parametrize(
    ("failure", "retried"),
    [
        (status_error(429), True),
        (status_error(529), True),
        (connection_error(), True),
        (status_error(400), False),
        (status_error(401), False),
        (reply(FAMILY_PLAN, stop_reason="refusal", refusal_category="cyber"), False),
        (reply(FAMILY_PLAN, stop_reason="max_tokens"), False),
    ],
    ids=["429", "529", "connection", "400", "401", "refusal", "max_tokens"],
)
def test_provider_failures_are_retried_only_when_useful_then_fall_back(evidence, engine, failure, retried):
    sleeps: list[float] = []
    loop = _loop(
        evidence,
        engine,
        LLMRepairPlanner(evidence, FakeClient(failure)),
        sleeps,
        fallback_planner=ReplayPlanner(REPLAY_DIR),
    )

    run = loop.run(LLMPlanner(evidence, FakeClient(failure)), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW)

    failed = [a for a in run.attempts if a.status is RunStatus.PLANNER_FAILED]
    assert len(failed) == (3 if retried else 1)
    assert sleeps == ([2.0, 4.0] if retried else [])
    assert run.attempts[-1].kind is AttemptKind.FALLBACK
    assert run.status is LoopStatus.ACCEPTED


def test_refusal_message_names_the_category(evidence, engine, story_map):
    planner = LLMPlanner(evidence, FakeClient(reply(FAMILY_PLAN, stop_reason="refusal", refusal_category="cyber")))
    strategy = build_audience_strategy(evidence, AudienceType.FAMILY)
    pool = build_evidence_pool(engine, evidence, _ctx(evidence, AudienceType.FAMILY))

    with pytest.raises(PlannerError, match="category cyber") as excinfo:
        planner.plan(story_map, strategy, pool)
    assert excinfo.value.retryable is False


def test_missing_credentials_are_reported_without_retries(evidence, engine):
    auth_error = TypeError('"Could not resolve authentication method. Expected one of api_key..."')
    sleeps: list[float] = []

    run = _loop(evidence, engine, LLMRepairPlanner(evidence, FakeClient(auth_error)), sleeps).run(
        LLMPlanner(evidence, FakeClient(auth_error)), _ctx(evidence, AudienceType.FAMILY), FIXED_NOW
    )

    assert run.status is LoopStatus.PLANNER_UNAVAILABLE
    assert "ANTHROPIC_API_KEY" in run.attempts[0].planning_issues[0].message
    assert sleeps == []


def test_programming_errors_are_not_disguised_as_outages(evidence, engine, story_map):
    planner = LLMPlanner(evidence, FakeClient(TypeError("create() got an unexpected keyword argument 'x'")))

    with pytest.raises(TypeError, match="unexpected keyword"):
        run_planning(evidence, engine, planner, _ctx(evidence, AudienceType.FAMILY))


def test_missing_sdk_is_a_clear_non_retryable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(PlannerError, match=r"pip install -e \"\.\[llm\]\"") as excinfo:
        create_client()
    assert excinfo.value.retryable is False


def test_live_responses_can_be_recorded_and_replayed(evidence, engine, tmp_path):
    planner = RecordingPlanner(LLMPlanner(evidence, FakeClient(reply(SPOILER_PLAN))), tmp_path, clock=lambda: FIXED_NOW)
    repairer = RecordingRepairPlanner(
        LLMRepairPlanner(evidence, FakeClient(reply(HONEST_REPAIR))), tmp_path, clock=lambda: FIXED_NOW
    )
    live = _loop(evidence, engine, repairer).run(planner, _ctx(evidence), FIXED_NOW)

    replayed = _loop(evidence, engine, ReplayRepairPlanner(tmp_path / "repair_runs")).run(
        ReplayPlanner(tmp_path / "planner_runs"), _ctx(evidence), FIXED_NOW
    )

    assert live.status is replayed.status is LoopStatus.ACCEPTED
    assert replayed.final_proposal == live.final_proposal
    assert load_replay(tmp_path / "planner_runs" / "young_adult.json").planner_version.startswith("llm-planner/")


def test_llm_loop_makes_no_real_network_calls(evidence, engine, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)

    run = _loop(evidence, engine, LLMRepairPlanner(evidence, FakeClient(reply(HONEST_REPAIR)))).run(
        LLMPlanner(evidence, FakeClient(reply(SPOILER_PLAN))), _ctx(evidence), FIXED_NOW
    )

    assert run.status is LoopStatus.ACCEPTED


class TestCli:
    @pytest.fixture
    def fake_client(self, monkeypatch):
        client = FakeClient(reply(SPOILER_PLAN), reply(HONEST_REPAIR))
        monkeypatch.setattr("trailer_director.llm.create_client", lambda timeout_seconds: client)
        return client

    def test_repair_with_llm_planner_and_repairer_records_fixtures(self, fake_client, tmp_path, capsys):
        args = ["repair", "--audience", "young_adult", "--mode", "llm", "--repair-mode", "llm"]

        code = main([*args, "--record-dir", str(tmp_path), "--data-dir", str(DATA_DIR)])

        output = capsys.readouterr().out
        assert code == 0
        assert "initial_candidate TRL_YOUNG_ADULT_REPLAY from llm llm-planner/claude-opus-5-5/high" in output
        assert "repair_decision   CLIP_003: replace_clip" in output
        assert (tmp_path / "planner_runs" / "young_adult.json").exists()
        assert (tmp_path / "repair_runs" / "young_adult.json").exists()
        assert len(fake_client.calls) == 2

    def test_model_and_effort_flags_reach_the_request(self, fake_client, capsys):
        args = ["plan", "--audience", "young_adult", "--mode", "llm", "--model", "claude-sonnet-5-5", "--effort", "low"]

        main([*args, "--data-dir", str(DATA_DIR)])

        assert fake_client.calls[0]["model"] == "claude-sonnet-5-5"
        assert fake_client.calls[0]["output_config"]["effort"] == "low"


class TestRepairerFallback:
    """LLM repairer unavailable -> deterministic repairer, symmetric with the planner's replay fallback."""

    def _loop(self, evidence, engine, failure, sleeps, **kwargs):
        return _loop(
            evidence,
            engine,
            LLMRepairPlanner(evidence, FakeClient(failure)),
            sleeps,
            fallback_repairer=DeterministicRepairPlanner(evidence),
            **kwargs,
        )

    @pytest.mark.parametrize(("failure", "expected_sleeps"), [(status_error(529), [2.0, 4.0]), (status_error(401), [])])
    def test_unavailable_llm_repairer_falls_back_to_deterministic_repair(
        self, evidence, engine, failure, expected_sleeps
    ):
        sleeps: list[float] = []

        run = self._loop(evidence, engine, failure, sleeps).run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

        assert run.status is LoopStatus.ACCEPTED
        repair = run.attempts[-1]
        assert (repair.kind, repair.planner_mode, repair.status) == (AttemptKind.REPAIR, "mock", RunStatus.ELIGIBLE)
        assert sleeps == expected_sleeps
        assert "repair by mock mock-repair-2" in run.audit_trail[-3].detail
        assert run.fallbacks == ["repairer llm unavailable -> mock mock-repair-2"]
        assert run.audit_trail[-2].step == "fallback"

    def test_fallback_repairer_is_sticky_for_the_rest_of_the_run(self, evidence, engine):
        llm_client = FakeClient(status_error(401))
        tampered = json.loads(json.dumps(HONEST_REPAIR))
        tampered["clips"][0]["music_id"] = "MUS_02"
        calls = []

        class ScriptedFallback:
            mode, version = PlannerMode.MOCK, "scripted-fallback"

            def repair(self, request):
                calls.append(request.attempt)
                payload = tampered if request.attempt == 1 else HONEST_REPAIR
                return PlannerResponse(mode=self.mode, planner_version=self.version, payload=payload)

        loop = _loop(evidence, engine, LLMRepairPlanner(evidence, llm_client), fallback_repairer=ScriptedFallback())
        run = loop.run(ReplayPlanner(REPLAY_DIR), _ctx(evidence), FIXED_NOW)

        assert run.status is LoopStatus.ACCEPTED
        assert calls == [1, 2]
        assert len(llm_client.calls) == 1

    def test_cost_sheet_can_forbid_the_repair_fallback(self, evidence, engine):
        fallback = evidence.cost_sheet.fallback_strategy.model_copy(
            update={"on_provider_unavailable": "reject_with_report"}
        )
        strict = replace(evidence, cost_sheet=evidence.cost_sheet.model_copy(update={"fallback_strategy": fallback}))

        run = self._loop(strict, engine, status_error(401), []).run(ReplayPlanner(REPLAY_DIR), _ctx(strict), FIXED_NOW)

        assert run.status is LoopStatus.REPAIR_FAILED

    def test_cli_uses_the_deterministic_fallback(self, monkeypatch, capsys):
        monkeypatch.setattr("trailer_director.llm.create_client", lambda timeout_seconds: FakeClient(status_error(401)))

        code = main(
            [
                "repair",
                "--audience",
                "young_adult",
                "--mode",
                "replay",
                "--repair-mode",
                "llm",
                "--data-dir",
                str(DATA_DIR),
            ]
        )

        output = capsys.readouterr().out
        assert code == 0
        assert "planner_failure   repair (llm): model provider error 401" in output
        assert "(repair by mock mock-repair-2)" in output
