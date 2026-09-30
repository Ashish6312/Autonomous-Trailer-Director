"""The violation -> repair strategy table and the decisions derived from it."""

from itertools import product

import pytest
from support import EXAMPLES_DIR, REPLAY_DIR, context

from trailer_director.constraints import ConstraintSeverity, EntityType, ReasonCode
from trailer_director.domain import AudienceType
from trailer_director.domain.edit import TrailerCandidate
from trailer_director.planning import build_evidence_pool, normalize_planner_output
from trailer_director.planning.replay import load_replay
from trailer_director.repair import Component, RepairAction, RepairScope, decide_repairs
from trailer_director.repair.strategy import NON_BLOCKING, PERMITTED_CHANGES, STRATEGIES
from trailer_director.repair.verification import diagnose


def _proposal(evidence, audience, payload_overrides=None):
    payload = load_replay(REPLAY_DIR / f"{audience}.json").response
    proposal, issues = normalize_planner_output(payload, evidence, AudienceType(audience))
    assert issues == []
    return proposal


def _decisions(engine, proposal, ctx):
    return decide_repairs(proposal, *diagnose(proposal, engine.evaluate_trailer(proposal.candidate, ctx)))


def test_every_reason_code_is_either_repairable_or_non_blocking():
    mapped = {reason for reason, _ in STRATEGIES}

    assert mapped | NON_BLOCKING == set(ReasonCode)
    assert not mapped & NON_BLOCKING


def test_every_error_the_engine_produces_has_an_explicit_strategy(engine, evidence):
    produced = set()
    for audience, day, territory in product(
        AudienceType, ["2026-07-01", "2026-11-14", "2027-01-15"], ["IN", "IN-HR", "GB"]
    ):
        pool = build_evidence_pool(engine, evidence, context(audience.value, on=day, territory=territory))
        produced |= {
            (v.reason_code, v.entity_type) for e in pool.rejected for v in e.violations if v.severity == "error"
        }
    risky = TrailerCandidate.model_validate_json((EXAMPLES_DIR / "young_adult_risky.json").read_text(encoding="utf-8"))
    result = engine.evaluate_trailer(risky, context("young_adult", on="2026-11-14"))
    produced |= {(v.reason_code, v.entity_type) for v in result.errors}

    assert produced <= set(STRATEGIES)


def test_no_action_may_change_a_clips_purpose():
    assert all(Component.PURPOSE not in permitted for permitted in PERMITTED_CHANGES.values())


def test_expired_music_decision_keeps_the_shot(engine, evidence):
    proposal = _proposal(evidence, "young_adult")
    clips = [clip.model_copy(update={"music_id": "MUS_03"}) for clip in proposal.candidate.clips[:2]]
    proposal = proposal.model_copy(
        update={"candidate": TrailerCandidate(trailer_id=proposal.candidate.trailer_id, clips=clips)}
    )

    decisions = _decisions(engine, proposal, context("young_adult", on="2026-11-14"))

    assert [(d.clip_id, d.scope) for d in decisions] == [
        ("CLIP_001", RepairScope.MUSIC),
        ("CLIP_002", RepairScope.MUSIC),
    ]
    decision = decisions[0]
    assert decision.allowed_actions == [RepairAction.REPLACE_MUSIC, RepairAction.REMOVE_MUSIC]
    assert decision.preserve == [Component.SCENE, Component.TIMECODES, Component.DIALOGUE, Component.PURPOSE]
    assert decision.reason_code is ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED
    assert [(t.rule_id, t.entity_id) for t in decision.triggered_by] == [("RIGHTS_MUSIC_PROMO_001", "MUS_03")]


def test_spoiler_scene_decision_replaces_the_clip_and_covers_the_line(engine, evidence):
    proposal = _proposal(evidence, "young_adult")

    [decision] = _decisions(engine, proposal, context("young_adult", on="2026-11-14"))

    assert (decision.clip_id, decision.scope, decision.preferred_action) == (
        "CLIP_003",
        RepairScope.CLIP,
        RepairAction.REPLACE_CLIP,
    )
    assert [(t.rule_id, t.entity_type, t.entity_id) for t in decision.triggered_by] == [
        ("SPOILER_001", EntityType.SCENE, "SC10")
    ]
    assert [t.entity_id for t in decision.also_resolves] == ["DLG_042"]
    assert decision.preserve == [Component.PURPOSE]


def test_misleading_line_decision_recuts_in_scene_first(engine, evidence):
    proposal = _proposal(evidence, "young_adult")
    misleading = proposal.candidate.clips[1].model_copy(
        update={"source_in": 504_500, "source_out": 510_500, "dialogue_ids": ["DLG_034"]}
    )
    clips = [proposal.candidate.clips[0], misleading]
    proposal = proposal.model_copy(
        update={"candidate": TrailerCandidate(trailer_id=proposal.candidate.trailer_id, clips=clips)}
    )

    [decision] = _decisions(engine, proposal, context("young_adult", on="2026-11-14"))

    assert decision.reason_code is ReasonCode.MISLEADING_DIALOGUE_IN_CLIP
    assert decision.scope is RepairScope.DIALOGUE
    assert decision.allowed_actions[0] is RepairAction.RECUT_IN_SCENE
    assert Component.SCENE in decision.preserve


@pytest.mark.parametrize(
    ("reason", "entity", "first_action"),
    [
        (ReasonCode.SCENE_NOT_FOUND, EntityType.SCENE, RepairAction.REPLACE_CLIP),
        (ReasonCode.MUSIC_NOT_FOUND, EntityType.MUSIC, RepairAction.REPLACE_MUSIC),
        (ReasonCode.CONTRACT_RESTRICTION, EntityType.ACTOR, RepairAction.REPLACE_CLIP),
        (ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED, EntityType.ACTOR, RepairAction.REPLACE_CLIP),
        (ReasonCode.TRAILER_TOO_LONG, EntityType.TRAILER, RepairAction.DROP_CLIP),
    ],
)
def test_strategy_table_examples(reason, entity, first_action):
    strategy = STRATEGIES[(reason, entity)]

    assert strategy.actions[0] is first_action


def test_missing_source_is_never_invented():
    strategy = STRATEGIES[(ReasonCode.SCENE_NOT_FOUND, EntityType.SCENE)]

    assert set(strategy.actions) == {RepairAction.REPLACE_CLIP, RepairAction.DROP_CLIP}
    assert "never invent" in strategy.rationale


def test_decisions_trace_to_real_violations(engine, evidence):
    risky = TrailerCandidate.model_validate_json((EXAMPLES_DIR / "young_adult_risky.json").read_text(encoding="utf-8"))
    ctx = context("young_adult", on="2026-11-14")
    result = engine.evaluate_trailer(risky, ctx)
    proposal = _proposal(evidence, "young_adult").model_copy(update={"candidate": risky, "clip_rationales": []})

    decisions = decide_repairs(proposal, *diagnose(proposal, result))

    errors = {(v.clip_id, v.rule_id, v.reason_code, v.entity_id) for v in result.errors}
    for decision in decisions:
        assert decision.reason_code is decision.triggered_by[0].reason_code
        for ref in [*decision.triggered_by, *decision.also_resolves]:
            assert (decision.clip_id, ref.rule_id, ref.reason_code, ref.entity_id) in errors
    assert {d.clip_id for d in decisions} == {
        v.clip_id for v in result.errors if v.severity is ConstraintSeverity.ERROR
    }
