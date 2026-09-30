"""Inputs and outputs of the constraint engine."""

from datetime import date
from enum import StrEnum

from pydantic import computed_field

from trailer_director.domain import AudienceType
from trailer_director.domain.base import DomainModel
from trailer_director.domain.enums import RankedEnum
from trailer_director.domain.ids import TerritoryCode


class ConstraintSeverity(RankedEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class EntityType(StrEnum):
    SCENE = "scene"
    DIALOGUE = "dialogue"
    MUSIC = "music"
    ACTOR = "actor"
    CHARACTER = "character"
    PROP = "prop"
    CLIP = "clip"
    TRAILER = "trailer"


class ReasonCode(StrEnum):
    SCENE_NOT_FOUND = "SCENE_NOT_FOUND"
    DIALOGUE_NOT_FOUND = "DIALOGUE_NOT_FOUND"
    DIALOGUE_SCENE_MISMATCH = "DIALOGUE_SCENE_MISMATCH"
    MUSIC_NOT_FOUND = "MUSIC_NOT_FOUND"
    CLIP_STARTS_BEFORE_SCENE = "CLIP_STARTS_BEFORE_SCENE"
    CLIP_ENDS_AFTER_SCENE = "CLIP_ENDS_AFTER_SCENE"
    DIALOGUE_OUTSIDE_CLIP = "DIALOGUE_OUTSIDE_CLIP"
    UNDECLARED_DIALOGUE_IN_CLIP = "UNDECLARED_DIALOGUE_IN_CLIP"
    SPOILER_LEVEL_EXCEEDED = "SPOILER_LEVEL_EXCEEDED"
    CONTENT_SEVERITY_EXCEEDED = "CONTENT_SEVERITY_EXCEEDED"
    MISLEADING_DIALOGUE_IN_CLIP = "MISLEADING_DIALOGUE_IN_CLIP"
    MISLEADING_SCENE_CONTEXT = "MISLEADING_SCENE_CONTEXT"
    MISLEADING_RELATIONSHIP = "MISLEADING_RELATIONSHIP"
    PROMOTION_NOT_PERMITTED = "PROMOTION_NOT_PERMITTED"
    AUDIENCE_NOT_LICENSED = "AUDIENCE_NOT_LICENSED"
    TERRITORY_NOT_LICENSED = "TERRITORY_NOT_LICENSED"
    RIGHTS_NOT_YET_ACTIVE = "RIGHTS_NOT_YET_ACTIVE"
    PROMOTIONAL_RIGHTS_EXPIRED = "PROMOTIONAL_RIGHTS_EXPIRED"
    ACTOR_RIGHTS_MISSING = "ACTOR_RIGHTS_MISSING"
    CONTRACT_RESTRICTION = "CONTRACT_RESTRICTION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TRAILER_TOO_LONG = "TRAILER_TOO_LONG"
    HISTORICAL_PERFORMANCE = "HISTORICAL_PERFORMANCE"
    SUBTITLE_REVIEW_REQUIRED = "SUBTITLE_REVIEW_REQUIRED"
    SUBTITLE_READING_SPEED_HIGH = "SUBTITLE_READING_SPEED_HIGH"
    DIALECT_REVIEW_REQUIRED = "DIALECT_REVIEW_REQUIRED"
    CONTINUITY_ORDER_REVERSED = "CONTINUITY_ORDER_REVERSED"


class ConstraintContext(DomainModel):
    """The campaign a candidate is being judged for."""

    audience: AudienceType
    evaluation_date: date
    territory: TerritoryCode


class ConstraintViolation(DomainModel):
    """One finding from one rule. INFO findings are notes and never block."""

    rule_id: str
    severity: ConstraintSeverity
    entity_type: EntityType
    entity_id: str
    reason_code: ReasonCode
    message: str
    remediation: str | None = None
    clip_id: str | None = None
    details: dict[str, str] = {}


class EligibilityResult(DomainModel):
    subject_type: EntityType
    subject_id: str
    context: ConstraintContext
    violations: list[ConstraintViolation] = []

    @computed_field
    @property
    def eligible(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[ConstraintViolation]:
        return self._with(ConstraintSeverity.ERROR)

    @property
    def warnings(self) -> list[ConstraintViolation]:
        return self._with(ConstraintSeverity.WARNING)

    @property
    def reason_codes(self) -> set[ReasonCode]:
        return {violation.reason_code for violation in self.violations}

    def _with(self, severity: ConstraintSeverity) -> list[ConstraintViolation]:
        return [violation for violation in self.violations if violation.severity is severity]
