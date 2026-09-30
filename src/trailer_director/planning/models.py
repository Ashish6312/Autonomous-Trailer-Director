"""Planner inputs and outputs, and the record of one planning run."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from trailer_director.constraints import ConstraintContext, EligibilityResult
from trailer_director.domain import AudienceType, ValidationSeverity
from trailer_director.domain.base import DomainModel
from trailer_director.domain.edit import ClipId, TrailerCandidate
from trailer_director.domain.ids import EpisodeId, NonEmptyStr


class PlannerMode(StrEnum):
    MOCK = "mock"
    REPLAY = "replay"
    LLM = "llm"


class IssueCode(StrEnum):
    PLANNER_FAILED = "PLANNER_FAILED"
    REPLAY_EVIDENCE_MISMATCH = "REPLAY_EVIDENCE_MISMATCH"
    STORY_MAP_UNRESOLVED_REFERENCE = "STORY_MAP_UNRESOLVED_REFERENCE"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    MISSING_FIELD = "MISSING_FIELD"
    AUDIENCE_MISMATCH = "AUDIENCE_MISMATCH"
    INVALID_CLIP = "INVALID_CLIP"
    UNKNOWN_SCENE = "UNKNOWN_SCENE"
    UNKNOWN_DIALOGUE = "UNKNOWN_DIALOGUE"
    UNKNOWN_MUSIC = "UNKNOWN_MUSIC"
    UNKNOWN_EVIDENCE = "UNKNOWN_EVIDENCE"
    MISSING_RATIONALE = "MISSING_RATIONALE"
    EVIDENCE_WITHHELD = "EVIDENCE_WITHHELD"
    HOOK_NOT_IN_EVIDENCE = "HOOK_NOT_IN_EVIDENCE"
    HOOK_USES_INELIGIBLE_LINE = "HOOK_USES_INELIGIBLE_LINE"
    HOOK_NOT_IN_TRAILER = "HOOK_NOT_IN_TRAILER"


class PlanningIssue(DomainModel):
    """A problem with planning itself, as opposed to a constraint violation in a valid candidate."""

    severity: ValidationSeverity
    code: IssueCode
    location: str
    message: str


class TokenUsage(DomainModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    """The model that actually served the call (a server-side fallback may differ from the one requested)."""


class PlannerResponse(DomainModel):
    """What a planner returns. ``payload`` is untrusted JSON-like data until normalised."""

    mode: PlannerMode
    planner_version: NonEmptyStr
    payload: Any
    model_calls: int = 0
    usage: TokenUsage | None = None
    latency_seconds: float | None = None
    """Wall-clock time of a live model call; never set by mock or replay planners."""
    issues: list[PlanningIssue] = []


class ClipRationale(DomainModel):
    clip_id: ClipId
    reason: NonEmptyStr
    evidence_ids: list[str]


class PlanProposal(DomainModel):
    """A normalised planner proposal: every ID it mentions exists in the evidence."""

    audience: AudienceType
    title: NonEmptyStr
    hook: NonEmptyStr
    positioning: NonEmptyStr
    rationale: NonEmptyStr
    candidate: TrailerCandidate
    clip_rationales: list[ClipRationale]


class RunStatus(StrEnum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    INVALID_OUTPUT = "invalid_output"
    PLANNER_FAILED = "planner_failed"


class PoolSummary(DomainModel):
    eligible_scenes: int
    eligible_dialogue: int
    eligible_music: int
    rejected: int


class PlanningRun(DomainModel):
    run_id: NonEmptyStr
    created_at: datetime
    episode_id: EpisodeId
    evidence_version: NonEmptyStr
    evidence_fingerprint: NonEmptyStr
    context: ConstraintContext
    mode: PlannerMode
    planner_version: NonEmptyStr
    status: RunStatus
    pool: PoolSummary
    proposal: PlanProposal | None = None
    eligibility: EligibilityResult | None = None
    issues: list[PlanningIssue] = []
    estimated_duration_seconds: float | None = None
    model_calls: int
    estimated_model_cost: float
    currency: NonEmptyStr
    usage: TokenUsage | None = None
    latency_seconds: float | None = None
