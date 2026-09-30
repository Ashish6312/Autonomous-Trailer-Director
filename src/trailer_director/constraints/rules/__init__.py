from trailer_director.constraints.rules.accessibility import (
    DialectReviewRule,
    SubtitleReadingSpeedRule,
    SubtitleSafetyRule,
)
from trailer_director.constraints.rules.base import ClipRule, TrailerRule
from trailer_director.constraints.rules.content import ContentRatingRule
from trailer_director.constraints.rules.context import MisleadingContextRule
from trailer_director.constraints.rules.continuity import PropContinuityRule
from trailer_director.constraints.rules.performance import HistoricalPerformanceRule
from trailer_director.constraints.rules.relationship import RelationshipTruthRule
from trailer_director.constraints.rules.rights import ActorRightsRule, ContractRestrictionRule, MusicRightsRule
from trailer_director.constraints.rules.source import DialogueExistsRule, MusicExistsRule, SceneExistsRule
from trailer_director.constraints.rules.spoiler import SpoilerRule
from trailer_director.constraints.rules.timecode import ClipWithinSceneRule, DialogueTimingRule
from trailer_director.constraints.rules.trailer import TrailerDurationRule

DEFAULT_CLIP_RULES: tuple[ClipRule, ...] = (
    SceneExistsRule(),
    DialogueExistsRule(),
    MusicExistsRule(),
    ClipWithinSceneRule(),
    DialogueTimingRule(),
    SpoilerRule(),
    ContentRatingRule(),
    MisleadingContextRule(),
    RelationshipTruthRule(),
    MusicRightsRule(),
    ActorRightsRule(),
    ContractRestrictionRule(),
    SubtitleSafetyRule(),
    SubtitleReadingSpeedRule(),
    DialectReviewRule(),
    HistoricalPerformanceRule(),
)

DEFAULT_TRAILER_RULES: tuple[TrailerRule, ...] = (TrailerDurationRule(), PropContinuityRule())

__all__ = ["DEFAULT_CLIP_RULES", "DEFAULT_TRAILER_RULES", "ClipRule", "TrailerRule"]
