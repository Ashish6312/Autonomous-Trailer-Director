"""Records for the verify -> decide -> repair -> re-verify loop."""

from datetime import datetime
from enum import StrEnum

from trailer_director.constraints import (
    ConstraintContext,
    ConstraintViolation,
    EligibilityResult,
    EntityType,
    ReasonCode,
)
from trailer_director.domain import ValidationSeverity
from trailer_director.domain.base import DomainModel
from trailer_director.domain.ids import EpisodeId, NonEmptyStr
from trailer_director.planning import (
    AudienceStrategy,
    EvidencePool,
    PlanningIssue,
    PlanProposal,
    PoolSummary,
    RunStatus,
    TokenUsage,
)
from trailer_director.story import StoryMap


class AttemptKind(StrEnum):
    INITIAL = "initial"
    REVERIFY = "reverify"
    RETRY = "retry"
    FALLBACK = "fallback"
    REPAIR = "repair"


class Component(StrEnum):
    """The parts of a clip a repair can change."""

    SCENE = "scene"
    TIMECODES = "timecodes"
    DIALOGUE = "dialogue"
    MUSIC = "music"
    PURPOSE = "purpose"


class RepairScope(StrEnum):
    MUSIC = "music"
    DIALOGUE = "dialogue"
    CLIP = "clip"
    TRAILER = "trailer"


class RepairAction(StrEnum):
    REPLACE_MUSIC = "replace_music"
    REMOVE_MUSIC = "remove_music"
    RECUT_IN_SCENE = "recut_in_scene"
    REPLACE_CLIP = "replace_clip"
    DROP_CLIP = "drop_clip"


class ClipAction(StrEnum):
    """What actually happened to a clip, computed by comparing two plans."""

    KEPT = "kept"
    MUSIC_SWAPPED = "music_swapped"
    MUSIC_REMOVED = "music_removed"
    RECUT = "recut"
    REPLACED = "replaced"
    DROPPED = "dropped"
    ADDED = "added"


class LoopStatus(StrEnum):
    ACCEPTED = "accepted"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PLANNER_UNAVAILABLE = "planner_unavailable"
    REPAIR_FAILED = "repair_failed"


class Verdict(StrEnum):
    """What can ship: an accepted plan, with or without findings a person must review, or nothing."""

    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    REJECTED = "REJECTED"


class VerificationCode(StrEnum):
    KEPT_CLIP_CHANGED = "KEPT_CLIP_CHANGED"
    FAILING_CLIP_UNCHANGED = "FAILING_CLIP_UNCHANGED"
    DECISION_VIOLATED = "DECISION_VIOLATED"
    PURPOSE_CHANGED = "PURPOSE_CHANGED"
    NO_PROGRESS = "NO_PROGRESS"
    RATIONALE_MISSING_SOURCE = "RATIONALE_MISSING_SOURCE"


class VerificationIssue(DomainModel):
    severity: ValidationSeverity
    code: VerificationCode
    location: str
    message: str


class ClipDiagnosis(DomainModel):
    """Why one clip of a candidate is not acceptable."""

    clip_id: str
    scene_id: str
    errors: list[ConstraintViolation]

    @property
    def music_only(self) -> bool:
        """Every error is about the clip's music, so the footage can stay."""
        return all(error.entity_type is EntityType.MUSIC for error in self.errors)

    @property
    def reason_codes(self) -> list[str]:
        return sorted({error.reason_code for error in self.errors})


class ViolationRef(DomainModel):
    rule_id: str
    reason_code: ReasonCode
    entity_type: EntityType
    entity_id: str


class RepairDecision(DomainModel):
    """What may change in one clip, and which violations require it."""

    clip_id: str
    scope: RepairScope
    reason_code: ReasonCode
    triggered_by: list[ViolationRef]
    also_resolves: list[ViolationRef] = []
    preferred_action: RepairAction
    allowed_actions: list[RepairAction]
    preserve: list[Component]
    """Kept by the preferred action. Fallbacks may change more, never the purpose."""
    rationale: str


class ClipChange(DomainModel):
    clip_id: str
    action: ClipAction
    changed: list[Component] = []
    previous_scene_id: str | None = None
    scene_id: str | None = None


class RepairRequest(DomainModel):
    """Everything a repair planner may use. Only what ``decisions`` allow may change."""

    attempt: int
    story_map: StoryMap
    strategy: AudienceStrategy
    pool: EvidencePool
    proposal: PlanProposal | None
    failing_clips: list[ClipDiagnosis]
    trailer_errors: list[ConstraintViolation]
    decisions: list[RepairDecision]
    planning_issues: list[PlanningIssue]
    excluded_ids: list[str]


class Attempt(DomainModel):
    number: int
    kind: AttemptKind
    planner_mode: str
    planner_version: str
    status: RunStatus
    proposal: PlanProposal | None = None
    eligibility: EligibilityResult | None = None
    failing_clips: list[ClipDiagnosis] = []
    decisions: list[RepairDecision] = []
    changes: list[ClipChange] = []
    planning_issues: list[PlanningIssue] = []
    verification_issues: list[VerificationIssue] = []
    model_calls: int = 0
    estimated_cost: float = 0.0
    usage: TokenUsage | None = None
    latency_seconds: float | None = None


class AuditStep(StrEnum):
    INITIAL_CANDIDATE = "initial_candidate"
    PLANNER_FAILURE = "planner_failure"
    FALLBACK = "fallback"
    INVALID_OUTPUT = "invalid_output"
    VERIFICATION = "verification"
    VIOLATION = "violation"
    REPAIR_DECISION = "repair_decision"
    CHANGE = "change"
    REPAIR_REJECTED = "repair_rejected"
    REVERIFICATION = "reverification"
    FINAL_DECISION = "final_decision"


class AuditEntry(DomainModel):
    step: AuditStep
    attempt: int | None
    detail: str
    refs: list[str] = []


class BudgetUsage(DomainModel):
    model_calls: int
    max_model_calls: int
    estimated_cost: float
    max_estimated_cost: float
    input_tokens: int = 0
    output_tokens: int = 0
    currency: str
    repair_attempts: int
    max_repair_attempts: int


class RepairRun(DomainModel):
    run_id: NonEmptyStr
    created_at: datetime
    episode_id: EpisodeId
    evidence_fingerprint: NonEmptyStr
    context: ConstraintContext
    status: LoopStatus
    verdict: Verdict
    pool: PoolSummary
    """What evidence the planner and repairer could use in this context."""
    fallbacks: list[str] = []
    """Every switch to a fallback planner or repairer, in order; empty when the primary stages did all the work."""
    final_proposal: PlanProposal | None
    final_eligibility: EligibilityResult | None
    attempts: list[Attempt]
    audit_trail: list[AuditEntry]
    budget: BudgetUsage
    summary: str
