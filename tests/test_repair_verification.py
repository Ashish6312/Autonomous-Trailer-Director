"""Diagnosis, change detection, the selective-replanning guard and the budget ledger."""

from dataclasses import replace

import pytest
from support import REPLAY_DIR, context

from trailer_director.domain import AudienceType, ValidationSeverity
from trailer_director.domain.edit import TrailerCandidate
from trailer_director.planning import build_audience_strategy, build_evidence_pool, normalize_planner_output
from trailer_director.planning.replay import load_replay
from trailer_director.repair import BudgetLedger, ClipAction, VerificationCode, decide_repairs
from trailer_director.repair.budget import PLANNER_CALL_ITEM, REPAIR_CALL_ITEM
from trailer_director.repair.verification import check_rationales, check_repair, clip_changes, diagnose


@pytest.fixture(scope="module")
def young_adult_replay(evidence):
    payload = load_replay(REPLAY_DIR / "young_adult.json").response
    proposal, issues = normalize_planner_output(payload, evidence, AudienceType.YOUNG_ADULT)
    assert issues == []
    return proposal


def _decisions_for(engine, proposal):
    result = engine.evaluate_trailer(proposal.candidate, context("young_adult", on="2026-11-14"))
    return decide_repairs(proposal, *diagnose(proposal, result))


def _with_clips(proposal, clips):
    candidate = TrailerCandidate(trailer_id=proposal.candidate.trailer_id, clips=clips)
    return proposal.model_copy(update={"candidate": candidate})


def test_diagnosis_points_at_the_failing_clip_only(engine, young_adult_replay):
    result = engine.evaluate_trailer(young_adult_replay.candidate, context("young_adult", on="2026-11-14"))

    failing, trailer_errors = diagnose(young_adult_replay, result)

    assert [(d.clip_id, d.scene_id) for d in failing] == [("CLIP_003", "SC10")]
    assert failing[0].reason_codes == ["SPOILER_LEVEL_EXCEEDED"]
    assert not failing[0].music_only
    assert trailer_errors == []


def test_expired_music_is_diagnosed_as_music_only(engine, young_adult_replay):
    clips = [clip.model_copy(update={"music_id": "MUS_03"}) for clip in young_adult_replay.candidate.clips[:2]]
    proposal = _with_clips(young_adult_replay, clips)

    failing, _ = diagnose(
        proposal, engine.evaluate_trailer(proposal.candidate, context("young_adult", on="2026-11-14"))
    )

    assert [d.clip_id for d in failing] == ["CLIP_001", "CLIP_002"]
    assert all(d.music_only for d in failing)


def test_changes_are_computed_from_the_plans(young_adult_replay):
    first, second, third = young_adult_replay.candidate.clips
    revised = _with_clips(
        young_adult_replay,
        [
            first,
            second.model_copy(update={"music_id": "MUS_02"}),
            third.model_copy(update={"clip_id": "CLIP_004"}),
        ],
    )

    changes = {c.clip_id: c.action for c in clip_changes(young_adult_replay, revised)}

    assert changes == {
        "CLIP_001": ClipAction.KEPT,
        "CLIP_002": ClipAction.MUSIC_SWAPPED,
        "CLIP_003": ClipAction.DROPPED,
        "CLIP_004": ClipAction.ADDED,
    }


def test_repair_may_not_touch_passing_clips(engine, young_adult_replay):
    first, second, third = young_adult_replay.candidate.clips
    revised = _with_clips(young_adult_replay, [first.model_copy(update={"music_id": "MUS_02"}), second, third])

    decisions = _decisions_for(engine, young_adult_replay)
    issues = check_repair(young_adult_replay, revised, decisions, clip_changes(young_adult_replay, revised))

    assert {(i.code, i.location) for i in issues} == {
        (VerificationCode.KEPT_CLIP_CHANGED, "CLIP_001"),
        (VerificationCode.FAILING_CLIP_UNCHANGED, "CLIP_003"),
    }


def test_identical_repair_is_no_progress(engine, young_adult_replay):
    changes = clip_changes(young_adult_replay, young_adult_replay)

    decisions = _decisions_for(engine, young_adult_replay)
    codes = {i.code for i in check_repair(young_adult_replay, young_adult_replay, decisions, changes)}

    assert VerificationCode.NO_PROGRESS in codes


def test_rationale_must_cite_the_clip_source(young_adult_replay):
    rationale = young_adult_replay.clip_rationales[0].model_copy(update={"evidence_ids": ["CMP_PRELAUNCH_CLIPS"]})
    proposal = young_adult_replay.model_copy(
        update={"clip_rationales": [rationale, *young_adult_replay.clip_rationales[1:]]}
    )

    [issue] = check_rationales(proposal)

    assert (issue.severity, issue.code, issue.location) == (
        ValidationSeverity.WARNING,
        VerificationCode.RATIONALE_MISSING_SOURCE,
        "CLIP_001",
    )
    assert "SC05" in issue.message


def test_budget_ledger_uses_cost_sheet_rates_and_limits(evidence):
    limits = evidence.cost_sheet.limits.model_copy(update={"max_model_calls": 2})
    ledger = BudgetLedger(evidence.cost_sheet.model_copy(update={"limits": limits}), max_repair_attempts=3)

    ledger.charge(PLANNER_CALL_ITEM, 1)
    ledger.charge(REPAIR_CALL_ITEM, 1)

    assert ledger.usage().estimated_cost == pytest.approx(0.065)
    assert not ledger.can_afford(REPAIR_CALL_ITEM, 1)
    assert ledger.can_afford(REPAIR_CALL_ITEM, 0)


def test_pool_and_strategy_are_reused_unchanged(evidence, engine):
    """The repair layer builds on the planning inputs rather than its own copies."""
    ctx = context("young_adult")
    assert build_evidence_pool(engine, evidence, ctx) == build_evidence_pool(engine, replace(evidence), ctx)
    assert build_audience_strategy(evidence, AudienceType.YOUNG_ADULT).hook_types[0] == "conflict"
