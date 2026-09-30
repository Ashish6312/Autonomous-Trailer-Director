"""Contractual clearances for music and performers."""

from datetime import date
from typing import Self

from pydantic import Field, model_validator

from trailer_director.domain.base import DomainModel, ensure_unique
from trailer_director.domain.enums import AudienceType, MusicType, PromotionalUse, RestrictionEffect
from trailer_director.domain.ids import ActorId, CharacterId, MusicId, NonEmptyStr, SceneId, TerritoryCode


class RightsWindow(DomainModel):
    """Where, for whom and when an asset may be used in promotion."""

    territories: list[TerritoryCode] = Field(min_length=1)
    allowed_audiences: list[AudienceType]
    valid_from: date
    valid_until: date

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.valid_until < self.valid_from:
            raise ValueError(f"valid_until {self.valid_until} is before valid_from {self.valid_from}")
        ensure_unique(self.territories, "territories")
        ensure_unique(self.allowed_audiences, "allowed_audiences")
        return self

    def is_active_on(self, day: date) -> bool:
        return self.valid_from <= day <= self.valid_until

    def allows_audience(self, audience: AudienceType) -> bool:
        return audience in self.allowed_audiences

    def covers_territory(self, territory: str) -> bool:
        """A licence for a country (``IN``) covers its subdivisions (``IN-HR``), not the reverse."""
        country = territory.split("-", 1)[0]
        return any(licensed in ("WORLDWIDE", territory, country) for licensed in self.territories)


class MusicAsset(RightsWindow):
    music_id: MusicId
    title: NonEmptyStr
    type: MusicType
    mood: list[NonEmptyStr] = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    allowed_for_promotion: bool
    notes: NonEmptyStr


class Restriction(DomainModel):
    code: NonEmptyStr
    description: NonEmptyStr
    effect: RestrictionEffect
    scene_ids: list[SceneId] = []


class ActorRights(RightsWindow):
    actor_id: ActorId
    performer_name: NonEmptyStr
    character_id: CharacterId
    promotional_use: PromotionalUse
    restrictions: list[Restriction] = []
