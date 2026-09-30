"""Audit trail: the run told in order, from initial candidate to final decision.

initial candidate -> verification -> violations -> repair decisions ->
changed components -> re-verification -> ... -> final decision.
Built only from recorded attempts, so it cannot disagree with them.
"""

from trailer_director.planning import RunStatus
from trailer_director.repair.models import Attempt, AttemptKind, AuditEntry, AuditStep, ClipAction


def build_audit_trail(attempts: list[Attempt], summary: str, fallbacks: list[str] | None = None) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    candidate_recorded = False
    for attempt in attempts:
        if attempt.status is RunStatus.PLANNER_FAILED:
            messages = "; ".join(issue.message for issue in attempt.planning_issues)
            entries.append(
                _entry(AuditStep.PLANNER_FAILURE, attempt, f"{attempt.kind} ({attempt.planner_mode}): {messages}")
            )
            continue
        if attempt.kind is AttemptKind.REPAIR:
            entries += _decisions(attempt) + _changes(attempt)
        elif attempt.proposal is not None and not candidate_recorded:
            clips = ", ".join(f"{c.clip_id} {c.scene_id}" for c in attempt.proposal.candidate.clips)
            source = (
                "the existing plan, re-verified under new conditions"
                if attempt.kind is AttemptKind.REVERIFY
                else f"{attempt.planner_mode} {attempt.planner_version}"
            )
            detail = f"{attempt.proposal.candidate.trailer_id} from {source}: {clips}"
            entries.append(_entry(AuditStep.INITIAL_CANDIDATE, attempt, detail))
            candidate_recorded = True

        if attempt.status is RunStatus.INVALID_OUTPUT:
            entries.append(_invalid(attempt))
            continue
        step = AuditStep.REVERIFICATION if attempt.kind is AttemptKind.REPAIR else AuditStep.VERIFICATION
        result = attempt.eligibility
        detail = f"{attempt.status}: {len(result.errors)} error(s), {len(result.warnings)} warning(s)"
        if attempt.kind is AttemptKind.REPAIR:
            detail += f" (repair by {attempt.planner_mode} {attempt.planner_version})"
        entries.append(_entry(step, attempt, detail))
        entries += [
            _entry(
                AuditStep.VIOLATION,
                attempt,
                f"{error.clip_id or 'trailer'}: {error.rule_id} {error.reason_code} "
                f"on {error.entity_type} {error.entity_id}",
                [error.rule_id, error.entity_id],
            )
            for error in result.errors
        ]
    entries += [AuditEntry(step=AuditStep.FALLBACK, attempt=None, detail=detail) for detail in fallbacks or []]
    entries.append(AuditEntry(step=AuditStep.FINAL_DECISION, attempt=None, detail=summary))
    return entries


def _decisions(attempt: Attempt) -> list[AuditEntry]:
    entries = []
    for decision in attempt.decisions:
        triggers = ", ".join(f"{t.rule_id}/{t.entity_id}" for t in decision.triggered_by)
        covered = ", ".join(f"{t.rule_id}/{t.entity_id}" for t in decision.also_resolves)
        detail = (
            f"{decision.clip_id}: {decision.preferred_action} (allowed: {', '.join(decision.allowed_actions)}; "
            f"preserve: {', '.join(decision.preserve)}) because {decision.reason_code} [{triggers}]"
        )
        if covered:
            detail += f"; also resolves [{covered}]"
        refs = [ref for t in decision.triggered_by for ref in (t.rule_id, t.entity_id)]
        entries.append(_entry(AuditStep.REPAIR_DECISION, attempt, detail, refs))
    return entries


def _changes(attempt: Attempt) -> list[AuditEntry]:
    entries = []
    kept = [c.clip_id for c in attempt.changes if c.action is ClipAction.KEPT and not c.changed]
    for change in attempt.changes:
        if change.clip_id in kept:
            continue
        detail = f"{change.clip_id} {change.action}"
        if change.changed:
            detail += f": changed {', '.join(change.changed)}"
        if change.action is ClipAction.REPLACED:
            detail += f" ({change.previous_scene_id} -> {change.scene_id})"
        entries.append(_entry(AuditStep.CHANGE, attempt, detail, [change.clip_id]))
    if kept:
        entries.append(_entry(AuditStep.CHANGE, attempt, f"kept unchanged: {', '.join(kept)}", kept))
    return entries


def _invalid(attempt: Attempt) -> AuditEntry:
    blocking = [i for i in attempt.verification_issues if i.severity == "error"]
    if blocking:
        detail = "; ".join(f"{i.code} {i.location}: {i.message}" for i in blocking)
        return _entry(AuditStep.REPAIR_REJECTED, attempt, detail)
    detail = "; ".join(f"{i.code} {i.location}" for i in attempt.planning_issues if i.severity == "error")
    return _entry(AuditStep.INVALID_OUTPUT, attempt, detail)


def _entry(step: AuditStep, attempt: Attempt, detail: str, refs: list[str] | None = None) -> AuditEntry:
    return AuditEntry(step=step, attempt=attempt.number, detail=detail, refs=refs or [])
