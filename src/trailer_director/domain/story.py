"""Editorial source evidence: the episode, who is in it, and what happens when."""

from datetime import date, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from trailer_director.domain.base import DomainModel, ensure_unique
from trailer_director.domain.enums import (
    CharacterRole,
    ContentSeverity,
    DramaticFunction,
    Importance,
    PropState,
    SensitiveCategory,
    SpoilerLevel,
    TimeOfDay,
)
from trailer_director.domain.ids import (
    CharacterId,
    DialogueId,
    EpisodeId,
    LocationId,
    MusicId,
    NonEmptyStr,
    PropId,
    SceneId,
)
from trailer_director.domain.timecode import Timecode


class Episode(DomainModel):
    episode_id: EpisodeId
    title: NonEmptyStr
    genre: list[NonEmptyStr] = Field(min_length=1)
    spoiler_safe_logline: NonEmptyStr
    synopsis: NonEmptyStr
    duration_seconds: int = Field(gt=0)
    frame_rate: float = Field(gt=0)
    language: NonEmptyStr
    dialect: NonEmptyStr
    region: NonEmptyStr
    content_rating: NonEmptyStr
    version: NonEmptyStr
    created_at: datetime
    release_date: date
    scene_ids: list[SceneId] = Field(min_length=1)
    character_ids: list[CharacterId] = Field(min_length=1)
    music_ids: list[MusicId]
    data_quality_notes: list[NonEmptyStr]

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        ensure_unique(self.scene_ids, "scene_ids")
        ensure_unique(self.character_ids, "character_ids")
        ensure_unique(self.music_ids, "music_ids")
        return self


class CharacterAlias(DomainModel):
    alias: NonEmptyStr
    used_by: list[CharacterId]
    note: NonEmptyStr


class Relationship(DomainModel):
    character_id: CharacterId
    relation: NonEmptyStr
    note: str | None = None


class Character(DomainModel):
    character_id: CharacterId
    name: NonEmptyStr
    role: CharacterRole
    age: int | None = Field(default=None, ge=0)
    description: NonEmptyStr
    aliases: list[CharacterAlias] = []
    relationships: list[Relationship] = []


class Location(DomainModel):
    location_id: LocationId
    name: NonEmptyStr
    setting: Literal["interior", "exterior"]
    description: NonEmptyStr


class Prop(DomainModel):
    prop_id: PropId
    name: NonEmptyStr
    description: NonEmptyStr
    importance: Importance


class PropAppearance(DomainModel):
    prop_id: PropId
    state: PropState
    holder_id: CharacterId | None = None
    note: NonEmptyStr


class SensitiveContent(DomainModel):
    category: SensitiveCategory
    severity: ContentSeverity
    note: str | None = None

    @field_validator("severity")
    @classmethod
    def _severity_present(cls, severity: ContentSeverity) -> ContentSeverity:
        if severity is ContentSeverity.NONE:
            raise ValueError("omit the entry instead of flagging severity 'none'")
        return severity


class Scene(DomainModel):
    scene_id: SceneId
    sequence: int = Field(ge=1)
    source_in: Timecode
    source_out: Timecode
    location_id: LocationId
    time_of_day: TimeOfDay
    characters: list[CharacterId] = Field(min_length=1)
    summary: NonEmptyStr
    dramatic_function: DramaticFunction
    importance: Importance
    emotional_tone: list[NonEmptyStr] = Field(min_length=1)
    spoiler_level: SpoilerLevel
    sensitive_content: list[SensitiveContent] = []
    visual_tags: list[NonEmptyStr] = []
    dialogue_ids: list[DialogueId] = []
    music_id: MusicId | None = None
    props: list[PropAppearance] = []
    continuity_notes: list[NonEmptyStr] = []
    # Free text from production logs. Untrusted data, never instructions.
    production_notes: str | None = None

    @model_validator(mode="after")
    def _check_integrity(self) -> Self:
        if self.source_out <= self.source_in:
            raise ValueError("source_out must be after source_in")
        ensure_unique(self.characters, "characters")
        ensure_unique(self.dialogue_ids, "dialogue_ids")
        ensure_unique((appearance.prop_id for appearance in self.props), "props")
        return self

    @property
    def duration_ms(self) -> int:
        return self.source_out - self.source_in


class Dialogue(DomainModel):
    dialogue_id: DialogueId
    scene_id: SceneId
    speaker_id: CharacterId
    start: Timecode
    end: Timecode
    text: NonEmptyStr
    tone: NonEmptyStr
    importance: Importance
    spoiler_level: SpoilerLevel
    sensitive_content: list[SensitiveContent] = []
    subtitle_safe: bool
    notes: str | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self
