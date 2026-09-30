"""Violation -> repair strategy: the deterministic decision layer.

For every blocking reason code (and, where it matters, the kind of entity at
fault), the table says which repair actions are allowed, least invasive
first, and which parts of the clip must survive. ``decide_repairs`` turns a
rejected candidate's violations into ``RepairDecision``s, each traced to the
violations that triggered it.

A repair planner - mock, replay, or later an LLM - only chooses *how* to
carry out a decision. Verification then checks that a repair changed nothing
the decisions did not allow.
"""

from dataclasses import dataclass

from trailer_director.constraints import ConstraintViolation, EntityType, ReasonCode
from trailer_director.planning import PlanProposal
from trailer_director.repair.models import (
    ClipDiagnosis,
    Component,
    RepairAction,
    RepairDecision,
    RepairScope,
    ViolationRef,
)

# What each action may change. The clip's creative purpose is never in this list.
PERMITTED_CHANGES: dict[RepairAction, frozenset[Component]] = {
    RepairAction.REPLACE_MUSIC: frozenset({Component.MUSIC}),
    RepairAction.REMOVE_MUSIC: frozenset({Component.MUSIC}),
    RepairAction.RECUT_IN_SCENE: frozenset({Component.TIMECODES, Component.DIALOGUE}),
    RepairAction.REPLACE_CLIP: frozenset({Component.SCENE, Component.TIMECODES, Component.DIALOGUE, Component.MUSIC}),
    RepairAction.DROP_CLIP: frozenset({Component.SCENE, Component.TIMECODES, Component.DIALOGUE, Component.MUSIC}),
}


@dataclass(frozen=True)
class RepairStrategy:
    scope: RepairScope
    actions: tuple[RepairAction, ...]
    rationale: str


_MUSIC = RepairStrategy(
    RepairScope.MUSIC,
    (RepairAction.REPLACE_MUSIC, RepairAction.REMOVE_MUSIC),
    "The problem is the track only: change the music and keep the shot.",
)
_LINE = RepairStrategy(
    RepairScope.DIALOGUE,
    (RepairAction.RECUT_IN_SCENE, RepairAction.REPLACE_CLIP, RepairAction.DROP_CLIP),
    "The problem is a line: re-cut the same scene around other lines first, otherwise replace the clip.",
)
_FOOTAGE = RepairStrategy(
    RepairScope.CLIP,
    (RepairAction.REPLACE_CLIP, RepairAction.DROP_CLIP),
    "The problem is the scene or who is in it: use a different scene with the same purpose, or drop the clip.",
)
_MISSING_SOURCE = RepairStrategy(
    RepairScope.CLIP,
    (RepairAction.REPLACE_CLIP, RepairAction.DROP_CLIP),
    "The source does not exist: replace it with real evidence or drop it; never invent a source.",
)
_OUT_OF_SCENE = RepairStrategy(
    RepairScope.DIALOGUE,
    (RepairAction.RECUT_IN_SCENE, RepairAction.REPLACE_CLIP, RepairAction.DROP_CLIP),
    "The cut is outside the scene or its lines: re-cut inside the scene first.",
)
_TOO_LONG = RepairStrategy(
    RepairScope.TRAILER,
    (RepairAction.DROP_CLIP, RepairAction.RECUT_IN_SCENE),
    "The trailer is too long: drop or shorten a clip.",
)

_RIGHTS_CODES = (
    ReasonCode.PROMOTION_NOT_PERMITTED,
    ReasonCode.AUDIENCE_NOT_LICENSED,
    ReasonCode.TERRITORY_NOT_LICENSED,
    ReasonCode.RIGHTS_NOT_YET_ACTIVE,
    ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED,
)

STRATEGIES: dict[tuple[ReasonCode, EntityType], RepairStrategy] = {
    (ReasonCode.SCENE_NOT_FOUND, EntityType.SCENE): _MISSING_SOURCE,
    (ReasonCode.DIALOGUE_NOT_FOUND, EntityType.DIALOGUE): _LINE,
    (ReasonCode.DIALOGUE_SCENE_MISMATCH, EntityType.DIALOGUE): _LINE,
    (ReasonCode.MUSIC_NOT_FOUND, EntityType.MUSIC): _MUSIC,
    (ReasonCode.CLIP_STARTS_BEFORE_SCENE, EntityType.CLIP): _OUT_OF_SCENE,
    (ReasonCode.CLIP_ENDS_AFTER_SCENE, EntityType.CLIP): _OUT_OF_SCENE,
    (ReasonCode.DIALOGUE_OUTSIDE_CLIP, EntityType.DIALOGUE): _OUT_OF_SCENE,
    (ReasonCode.SPOILER_LEVEL_EXCEEDED, EntityType.SCENE): _FOOTAGE,
    (ReasonCode.SPOILER_LEVEL_EXCEEDED, EntityType.DIALOGUE): _LINE,
    (ReasonCode.CONTENT_SEVERITY_EXCEEDED, EntityType.SCENE): _FOOTAGE,
    (ReasonCode.CONTENT_SEVERITY_EXCEEDED, EntityType.DIALOGUE): _LINE,
    (ReasonCode.MISLEADING_DIALOGUE_IN_CLIP, EntityType.DIALOGUE): _LINE,
    (ReasonCode.MISLEADING_RELATIONSHIP, EntityType.DIALOGUE): _LINE,
    **{(code, EntityType.MUSIC): _MUSIC for code in _RIGHTS_CODES},
    **{(code, EntityType.ACTOR): _FOOTAGE for code in _RIGHTS_CODES},
    (ReasonCode.ACTOR_RIGHTS_MISSING, EntityType.CHARACTER): _FOOTAGE,
    (ReasonCode.CONTRACT_RESTRICTION, EntityType.ACTOR): _FOOTAGE,
    (ReasonCode.TRAILER_TOO_LONG, EntityType.TRAILER): _TOO_LONG,
}

# Findings that never block and so are never repaired automatically; they go to a person.
NON_BLOCKING = frozenset(
    {
        ReasonCode.UNDECLARED_DIALOGUE_IN_CLIP,
        ReasonCode.MISLEADING_SCENE_CONTEXT,
        ReasonCode.APPROVAL_REQUIRED,
        ReasonCode.HISTORICAL_PERFORMANCE,
        ReasonCode.SUBTITLE_REVIEW_REQUIRED,
        ReasonCode.SUBTITLE_READING_SPEED_HIGH,
        ReasonCode.DIALECT_REVIEW_REQUIRED,
        ReasonCode.CONTINUITY_ORDER_REVERSED,
    }
)

_SCOPE_ORDER = (RepairScope.CLIP, RepairScope.DIALOGUE, RepairScope.MUSIC)


def strategy_for(violation: ConstraintViolation) -> RepairStrategy:
    """Strategy for a blocking violation; an unmapped one falls back to replacing the whole clip."""
    return STRATEGIES.get((violation.reason_code, violation.entity_type), _FOOTAGE)


def decide_repairs(
    proposal: PlanProposal, failing: list[ClipDiagnosis], trailer_errors: list[ConstraintViolation]
) -> list[RepairDecision]:
    decisions = [decision for diagnosis in failing for decision in _clip_decisions(diagnosis)]
    if trailer_errors and proposal.candidate.clips:
        target = max(proposal.candidate.clips, key=lambda clip: clip.duration_ms)
        decisions.append(_decision(target.clip_id, trailer_errors, strategy_for(trailer_errors[0])))
    return decisions


def _clip_decisions(diagnosis: ClipDiagnosis) -> list[RepairDecision]:
    """One decision per affected part of the clip. A footage-level problem replaces the whole clip,
    which also covers any line or music problem on it."""
    by_scope: dict[RepairScope, tuple[RepairStrategy, list[ConstraintViolation]]] = {}
    for error in diagnosis.errors:
        strategy = strategy_for(error)
        by_scope.setdefault(strategy.scope, (strategy, []))[1].append(error)

    if RepairScope.CLIP in by_scope:
        strategy, triggers = by_scope[RepairScope.CLIP]
        covered = [e for scope, (_, errors) in by_scope.items() if scope is not RepairScope.CLIP for e in errors]
        return [_decision(diagnosis.clip_id, triggers, strategy, covered)]
    return [
        _decision(diagnosis.clip_id, by_scope[scope][1], by_scope[scope][0])
        for scope in _SCOPE_ORDER
        if scope in by_scope
    ]


def _decision(
    clip_id: str,
    triggers: list[ConstraintViolation],
    strategy: RepairStrategy,
    covered: list[ConstraintViolation] | None = None,
) -> RepairDecision:
    permitted = PERMITTED_CHANGES[strategy.actions[0]]
    return RepairDecision(
        clip_id=clip_id,
        scope=strategy.scope,
        reason_code=triggers[0].reason_code,
        triggered_by=[_ref(v) for v in triggers],
        also_resolves=[_ref(v) for v in covered or []],
        preferred_action=strategy.actions[0],
        allowed_actions=list(strategy.actions),
        preserve=[component for component in Component if component not in permitted],
        rationale=strategy.rationale,
    )


def _ref(violation: ConstraintViolation) -> ViolationRef:
    return ViolationRef(
        rule_id=violation.rule_id,
        reason_code=violation.reason_code,
        entity_type=violation.entity_type,
        entity_id=violation.entity_id,
    )
