"""Audience rating policies and (synthetic) audience profiles."""

from typing import Self

from pydantic import Field, model_validator

from trailer_director.domain.base import DomainModel
from trailer_director.domain.enums import AudienceType, ContentSeverity, SensitiveCategory, SpoilerLevel
from trailer_director.domain.ids import CampaignId, NonEmptyStr


class ContentRule(DomainModel):
    category: SensitiveCategory
    max_severity: ContentSeverity
    guidance: NonEmptyStr


class RatingPolicy(DomainModel):
    audience: AudienceType
    certification_target: NonEmptyStr
    max_spoiler_level: SpoilerLevel
    max_trailer_duration_seconds: int = Field(gt=0)
    subtitles_required: bool
    dialect_review_required: bool
    content_rules: list[ContentRule]

    @model_validator(mode="after")
    def _one_rule_per_category(self) -> Self:
        categories = [rule.category for rule in self.content_rules]
        missing = sorted(set(SensitiveCategory) - set(categories))
        if missing:
            raise ValueError(f"content_rules missing categories: {', '.join(missing)}")
        if len(categories) != len(set(categories)):
            raise ValueError("content_rules define a category more than once")
        return self

    def max_severity_for(self, category: SensitiveCategory) -> ContentSeverity:
        return next(rule.max_severity for rule in self.content_rules if rule.category is category)


class AudienceEvidence(DomainModel):
    claim: NonEmptyStr
    source: NonEmptyStr
    campaign_ids: list[CampaignId] = []


class AudienceProfile(DomainModel):
    audience: AudienceType
    display_name: NonEmptyStr
    description: NonEmptyStr
    preferred_themes: list[NonEmptyStr] = Field(min_length=1)
    preferred_tones: list[NonEmptyStr] = Field(min_length=1)
    avoid: list[NonEmptyStr] = Field(min_length=1)
    pacing: NonEmptyStr
    target_trailer_duration_seconds: int = Field(gt=0)
    positioning_notes: NonEmptyStr
    evidence: list[AudienceEvidence] = Field(min_length=1)
    data_provenance: NonEmptyStr
