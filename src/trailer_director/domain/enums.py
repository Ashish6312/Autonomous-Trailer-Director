from enum import StrEnum


class RankedEnum(StrEnum):
    """String enum whose declaration order defines severity, lowest first."""

    @property
    def rank(self) -> int:
        return list(type(self)).index(self)


class AudienceType(StrEnum):
    FAMILY = "family"
    YOUNG_ADULT = "young_adult"
    DIALECT_REGION = "dialect_region"


class SpoilerLevel(RankedEnum):
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


class ContentSeverity(RankedEnum):
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    STRONG = "strong"


class SensitiveCategory(StrEnum):
    FRIGHTENING = "frightening"
    SUGGESTIVE = "suggestive"
    VIOLENCE = "violence"
    STRONG_LANGUAGE = "strong_language"
    SENSITIVE_CONTEXT = "sensitive_context"
    MISLEADING_CONTEXT = "misleading_context"


class Importance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DramaticFunction(StrEnum):
    SETUP = "setup"
    INCITING_INCIDENT = "inciting_incident"
    RELATIONSHIP = "relationship"
    CONFLICT = "conflict"
    MYSTERY = "mystery"
    INVESTIGATION = "investigation"
    REVEAL = "reveal"
    RESOLUTION = "resolution"
    CLIFFHANGER = "cliffhanger"


class TimeOfDay(StrEnum):
    DAWN = "dawn"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class CharacterRole(StrEnum):
    LEAD = "lead"
    MAIN = "main"
    SUPPORTING = "supporting"
    REFERENCED = "referenced"


class PropState(StrEnum):
    INTRODUCED = "introduced"
    ON_SCREEN = "on_screen"
    HELD = "held"
    REFERENCED = "referenced"
    REVEALED = "revealed"
    DESTROYED = "destroyed"


class MusicType(StrEnum):
    SCORE = "score"
    SONG = "song"
    STINGER = "stinger"


class PromotionalUse(StrEnum):
    FULL = "full"
    LIMITED = "limited"
    PROHIBITED = "prohibited"


class RestrictionEffect(StrEnum):
    PROHIBIT = "prohibit"
    REQUIRES_APPROVAL = "requires_approval"


class CostCategory(StrEnum):
    MODEL_CALL = "model_call"
    VALIDATION_CALL = "validation_call"
    MEDIA_PROCESSING = "media_processing"
    TOOL_CALL = "tool_call"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
