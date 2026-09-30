"""Independent verification of a candidate and of what a repair changed.

The engine says whether the candidate is eligible. This module turns that
into per-clip diagnoses and checks the repair itself: changes are computed by
comparing plans component by component, never taken from the repair
planner's own account, and every change must be permitted by a repair
decision. Passing clips must come back unchanged and no clip may lose its
creative purpose.
"""

from trailer_director.constraints import ConstraintSeverity, ConstraintViolation, EligibilityResult
from trailer_director.domain import ValidationSeverity
from trailer_director.domain.edit import TrailerClip
from trailer_director.planning import PlanProposal
from trailer_director.repair.models import (
    ClipAction,
    ClipChange,
    ClipDiagnosis,
    Component,
    RepairAction,
    RepairDecision,
    VerificationCode,
    VerificationIssue,
)
from trailer_director.repair.strategy import PERMITTED_CHANGES


def diagnose(
    proposal: PlanProposal, eligibility: EligibilityResult
) -> tuple[list[ClipDiagnosis], list[ConstraintViolation]]:
    """Split errors into per-clip diagnoses and trailer-level errors."""
    errors = [v for v in eligibility.violations if v.severity is ConstraintSeverity.ERROR]
    diagnoses = [
        ClipDiagnosis(
            clip_id=clip.clip_id,
            scene_id=clip.scene_id,
            errors=[error for error in errors if error.clip_id == clip.clip_id],
        )
        for clip in proposal.candidate.clips
    ]
    trailer_errors = [error for error in errors if error.clip_id is None]
    return [d for d in diagnoses if d.errors], trailer_errors


def clip_changes(previous: PlanProposal, revised: PlanProposal) -> list[ClipChange]:
    before = {clip.clip_id: clip for clip in previous.candidate.clips}
    after = {clip.clip_id: clip for clip in revised.candidate.clips}
    changes = []
    for clip_id, old in before.items():
        new = after.get(clip_id)
        if new is None:
            changes.append(ClipChange(clip_id=clip_id, action=ClipAction.DROPPED, previous_scene_id=old.scene_id))
            continue
        changed = _changed_components(old, new)
        changes.append(
            ClipChange(
                clip_id=clip_id,
                action=_action(changed, new),
                changed=changed,
                previous_scene_id=old.scene_id,
                scene_id=new.scene_id,
            )
        )
    changes += [
        ClipChange(clip_id=clip_id, action=ClipAction.ADDED, scene_id=clip.scene_id)
        for clip_id, clip in after.items()
        if clip_id not in before
    ]
    return changes


def check_repair(
    previous: PlanProposal, revised: PlanProposal, decisions: list[RepairDecision], changes: list[ClipChange]
) -> list[VerificationIssue]:
    """Every change must be allowed by a decision; undecided clips and every clip's purpose must survive."""
    allowed: dict[str, set[RepairAction]] = {}
    for decision in decisions:
        allowed.setdefault(decision.clip_id, set()).update(decision.allowed_actions)

    issues = []
    for change in changes:
        actions = allowed.get(change.clip_id)
        if Component.PURPOSE in change.changed:
            issues.append(
                _error(VerificationCode.PURPOSE_CHANGED, change.clip_id, f"{change.clip_id} changed its purpose")
            )
        if change.action is ClipAction.ADDED:
            issues.append(
                _error(VerificationCode.DECISION_VIOLATED, change.clip_id, "no decision allows adding a new clip")
            )
        elif actions is None:
            if change.action is not ClipAction.KEPT or change.changed:
                issues.append(
                    _error(
                        VerificationCode.KEPT_CLIP_CHANGED,
                        change.clip_id,
                        f"{change.clip_id} had no violations and must be kept, but was {change.action}",
                    )
                )
        elif change.action is ClipAction.KEPT and not change.changed:
            issues.append(
                _error(
                    VerificationCode.FAILING_CLIP_UNCHANGED,
                    change.clip_id,
                    f"{change.clip_id} failed but was not changed",
                )
            )
        else:
            issues += _outside_decision(change, actions)
    if revised.candidate == previous.candidate:
        issues.append(_error(VerificationCode.NO_PROGRESS, "candidate", "repair returned the same candidate"))
    return issues


def check_rationales(proposal: PlanProposal) -> list[VerificationIssue]:
    """Each clip's rationale must cite the scene and lines the clip actually uses."""
    rationales = {r.clip_id: set(r.evidence_ids) for r in proposal.clip_rationales}
    issues = []
    for clip in proposal.candidate.clips:
        missing = sorted({clip.scene_id, *clip.dialogue_ids} - rationales.get(clip.clip_id, set()))
        if missing:
            issues.append(
                VerificationIssue(
                    severity=ValidationSeverity.WARNING,
                    code=VerificationCode.RATIONALE_MISSING_SOURCE,
                    location=clip.clip_id,
                    message=f"rationale does not cite {', '.join(missing)}",
                )
            )
    return issues


def _outside_decision(change: ClipChange, actions: set[RepairAction]) -> list[VerificationIssue]:
    if change.action is ClipAction.DROPPED:
        return [] if RepairAction.DROP_CLIP in actions else [_violated(change, "dropping the clip")]
    permitted = set().union(*(PERMITTED_CHANGES[action] for action in actions))
    extra = sorted(set(change.changed) - permitted - {Component.PURPOSE})
    return [_violated(change, f"changing {', '.join(extra)}")] if extra else []


def _violated(change: ClipChange, what: str) -> VerificationIssue:
    return _error(
        VerificationCode.DECISION_VIOLATED, change.clip_id, f"{change.clip_id}: no repair decision allows {what}"
    )


def _changed_components(old: TrailerClip, new: TrailerClip) -> list[Component]:
    pairs = {
        Component.SCENE: (old.scene_id, new.scene_id),
        Component.TIMECODES: ((old.source_in, old.source_out), (new.source_in, new.source_out)),
        Component.DIALOGUE: (old.dialogue_ids, new.dialogue_ids),
        Component.MUSIC: (old.music_id, new.music_id),
        Component.PURPOSE: (old.purpose, new.purpose),
    }
    return [component for component, (before, after) in pairs.items() if before != after]


def _action(changed: list[Component], new: TrailerClip) -> ClipAction:
    if Component.SCENE in changed:
        return ClipAction.REPLACED
    if Component.TIMECODES in changed or Component.DIALOGUE in changed:
        return ClipAction.RECUT
    if Component.MUSIC in changed:
        return ClipAction.MUSIC_SWAPPED if new.music_id else ClipAction.MUSIC_REMOVED
    return ClipAction.KEPT


def _error(code: VerificationCode, location: str, message: str) -> VerificationIssue:
    return VerificationIssue(severity=ValidationSeverity.ERROR, code=code, location=location, message=message)
