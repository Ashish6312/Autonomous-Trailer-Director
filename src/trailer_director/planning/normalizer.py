"""Trust boundary between any planner and the constraint engine.

Turns an untrusted JSON-like payload into a ``PlanProposal`` or a list of
structured issues. It checks shape and that every ID exists; it does not
judge eligibility, which stays with the constraint engine.
"""

from typing import Any

from pydantic import ValidationError

from trailer_director.domain import AudienceType, EpisodePackage, ValidationSeverity
from trailer_director.domain.edit import TrailerCandidate, TrailerClip
from trailer_director.planning.models import ClipRationale, IssueCode, PlanningIssue, PlanProposal

_TEXT_FIELDS = ("trailer_id", "title", "hook", "positioning", "rationale")
_RATIONALE_FIELDS = frozenset({"reason", "evidence"})
_CLIP_FIELDS = frozenset(TrailerClip.model_fields)


def normalize_planner_output(
    payload: Any, evidence: EpisodePackage, audience: AudienceType
) -> tuple[PlanProposal | None, list[PlanningIssue]]:
    issues: list[PlanningIssue] = []
    if not isinstance(payload, dict):
        return None, [_error(IssueCode.MALFORMED_OUTPUT, "output", "planner output must be a JSON object")]

    if "audience" not in payload:
        issues.append(_error(IssueCode.MISSING_FIELD, "output.audience", "audience is required"))
    elif payload["audience"] != audience:
        issues.append(
            _error(
                IssueCode.AUDIENCE_MISMATCH,
                "output.audience",
                f"planned for {payload['audience']!r} but {audience!r} was requested",
            )
        )
    for name in _TEXT_FIELDS:
        if not isinstance(payload.get(name), str) or not payload[name].strip():
            issues.append(_error(IssueCode.MISSING_FIELD, f"output.{name}", f"{name} must be a non-empty string"))

    raw_clips = payload.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips:
        issues.append(_error(IssueCode.MISSING_FIELD, "output.clips", "clips must be a non-empty list"))
        raw_clips = []

    clips: list[TrailerClip] = []
    rationales: list[ClipRationale] = []
    for index, raw_clip in enumerate(raw_clips):
        parsed = _normalize_clip(raw_clip, f"output.clips[{index}]", evidence, issues)
        if parsed is not None:
            clips.append(parsed[0])
            rationales.append(parsed[1])

    if any(issue.severity is ValidationSeverity.ERROR for issue in issues):
        return None, issues
    try:
        candidate = TrailerCandidate(trailer_id=payload["trailer_id"], clips=clips)
    except ValidationError as exc:
        return None, [*issues, *_pydantic_issues(exc, "output", IssueCode.MALFORMED_OUTPUT)]

    proposal = PlanProposal(
        audience=audience,
        title=payload["title"],
        hook=payload["hook"],
        positioning=payload["positioning"],
        rationale=payload["rationale"],
        candidate=candidate,
        clip_rationales=rationales,
    )
    return proposal, issues


def _normalize_clip(
    raw: Any, location: str, evidence: EpisodePackage, issues: list[PlanningIssue]
) -> tuple[TrailerClip, ClipRationale] | None:
    if not isinstance(raw, dict):
        issues.append(_error(IssueCode.MALFORMED_OUTPUT, location, "clip must be a JSON object"))
        return None
    for key in sorted(set(raw) - _CLIP_FIELDS - _RATIONALE_FIELDS):
        issues.append(_issue(ValidationSeverity.WARNING, IssueCode.MALFORMED_OUTPUT, f"{location}.{key}", "ignored"))

    try:
        clip = TrailerClip.model_validate({key: value for key, value in raw.items() if key in _CLIP_FIELDS})
    except ValidationError as exc:
        issues.extend(_pydantic_issues(exc, location, IssueCode.INVALID_CLIP))
        return None

    found_unknown = False
    references = [
        (IssueCode.UNKNOWN_SCENE, f"{location}.scene_id", clip.scene_id),
        *((IssueCode.UNKNOWN_DIALOGUE, f"{location}.dialogue_ids[{i}]", d) for i, d in enumerate(clip.dialogue_ids)),
        *([(IssueCode.UNKNOWN_MUSIC, f"{location}.music_id", clip.music_id)] if clip.music_id else []),
    ]
    for code, path, entity_id in references:
        if not evidence.has_entity(entity_id):
            issues.append(_error(code, path, f"'{entity_id}' does not exist in the evidence"))
            found_unknown = True

    rationale = _rationale(raw, clip.clip_id, location, evidence, issues)
    if found_unknown or rationale is None:
        return None
    return clip, rationale


def _rationale(
    raw: dict[str, Any], clip_id: str, location: str, evidence: EpisodePackage, issues: list[PlanningIssue]
) -> ClipRationale | None:
    reason, evidence_ids = raw.get("reason"), raw.get("evidence")
    valid = True
    if not isinstance(reason, str) or not reason.strip():
        issues.append(_error(IssueCode.MISSING_RATIONALE, f"{location}.reason", "every clip needs a reason"))
        valid = False
    if not isinstance(evidence_ids, list) or not evidence_ids or not all(isinstance(e, str) for e in evidence_ids):
        issues.append(
            _error(IssueCode.MISSING_RATIONALE, f"{location}.evidence", "every clip needs a list of evidence IDs")
        )
        return None
    for index, entity_id in enumerate(evidence_ids):
        if not evidence.has_entity(entity_id):
            issues.append(
                _error(IssueCode.UNKNOWN_EVIDENCE, f"{location}.evidence[{index}]", f"'{entity_id}' does not exist")
            )
            valid = False
    return ClipRationale(clip_id=clip_id, reason=reason, evidence_ids=evidence_ids) if valid else None


def _pydantic_issues(exc: ValidationError, location: str, code: IssueCode) -> list[PlanningIssue]:
    return [
        _error(code, location + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err["loc"]), err["msg"])
        for err in exc.errors(include_url=False)
    ]


def _error(code: IssueCode, location: str, message: str) -> PlanningIssue:
    return _issue(ValidationSeverity.ERROR, code, location, message)


def _issue(severity: ValidationSeverity, code: IssueCode, location: str, message: str) -> PlanningIssue:
    return PlanningIssue(severity=severity, code=code, location=location, message=message)
