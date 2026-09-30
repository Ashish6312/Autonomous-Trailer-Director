"""Audience strategy, derived from the audience profile and rating policy.

Profile preferences that match ``domain.language.stereotyping_phrases`` are
dropped and listed in ``withheld`` for cultural review. The avoid list is not
screened: naming a stereotype to avoid is not using it.
"""

import re

from trailer_director.domain import (
    AudienceEvidence,
    AudienceType,
    ContentSeverity,
    DramaticFunction,
    EpisodePackage,
    SensitiveCategory,
    SpoilerLevel,
)
from trailer_director.domain.base import DomainModel
from trailer_director.domain.language import stereotyping_phrases

# Planning heuristic, not audience data: which scene functions make the best
# openings for each audience, in priority order. The audience profiles describe
# preferences in prose; this table is the explicit, reviewable translation.
HOOK_TYPES: dict[AudienceType, tuple[DramaticFunction, ...]] = {
    AudienceType.FAMILY: (DramaticFunction.RELATIONSHIP, DramaticFunction.INCITING_INCIDENT, DramaticFunction.SETUP),
    AudienceType.YOUNG_ADULT: (DramaticFunction.CONFLICT, DramaticFunction.MYSTERY, DramaticFunction.CLIFFHANGER),
    AudienceType.DIALECT_REGION: (
        DramaticFunction.SETUP,
        DramaticFunction.INVESTIGATION,
        DramaticFunction.RELATIONSHIP,
    ),
}


class AudienceStrategy(DomainModel):
    audience: AudienceType
    display_name: str
    positioning: str
    preferred_themes: list[str]
    preferred_tones: list[str]
    avoid: list[str]
    pacing: str
    target_duration_seconds: int
    max_duration_seconds: int
    max_spoiler_level: SpoilerLevel
    content_limits: dict[SensitiveCategory, ContentSeverity]
    subtitles_required: bool
    dialect_review_required: bool
    hook_types: list[DramaticFunction]
    evidence: list[AudienceEvidence]
    withheld: list[str] = []
    """Profile preferences left out because they read as stereotypes; each needs cultural review."""

    @property
    def tone_words(self) -> set[str]:
        return {word for tone in self.preferred_tones for word in tone.lower().replace("-", " ").split()}


def build_audience_strategy(evidence: EpisodePackage, audience: AudienceType) -> AudienceStrategy:
    profile = evidence.audience_profile(audience)
    policy = evidence.rating_policy(audience)
    withheld: list[str] = []
    themes = _screened("preferred_themes", profile.preferred_themes, withheld)
    tones = _screened("preferred_tones", profile.preferred_tones, withheld)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", profile.positioning_notes) if s]
    positioning = " ".join(_screened("positioning_notes", sentences, withheld))
    return AudienceStrategy(
        audience=audience,
        display_name=profile.display_name,
        positioning=positioning,
        preferred_themes=themes,
        preferred_tones=tones,
        avoid=profile.avoid,
        pacing=profile.pacing,
        target_duration_seconds=profile.target_trailer_duration_seconds,
        max_duration_seconds=policy.max_trailer_duration_seconds,
        max_spoiler_level=policy.max_spoiler_level,
        content_limits={rule.category: rule.max_severity for rule in policy.content_rules},
        subtitles_required=policy.subtitles_required,
        dialect_review_required=policy.dialect_review_required,
        hook_types=list(HOOK_TYPES[audience]),
        evidence=profile.evidence,
        withheld=withheld,
    )


def _screened(field: str, items: list[str], withheld: list[str]) -> list[str]:
    kept = []
    for item in items:
        phrases = stereotyping_phrases(item)
        if phrases:
            withheld.append(f"{field}: '{item}' (stereotyping: {', '.join(phrases)})")
        else:
            kept.append(item)
    return kept
