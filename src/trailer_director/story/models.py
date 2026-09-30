"""The story map: a structured, source-referenced view of the episode for trailer planning.

Every record keeps the IDs of the evidence it came from, so any claim a
planner makes from it can be traced back and checked.
"""

from typing import Literal

from trailer_director.domain import (
    AudienceType,
    CharacterRole,
    DramaticFunction,
    Importance,
    SpoilerLevel,
)
from trailer_director.domain.base import DomainModel
from trailer_director.domain.ids import CampaignId, CharacterId, DialogueId, EpisodeId, NonEmptyStr, PropId, SceneId


class Premise(DomainModel):
    logline: NonEmptyStr
    scene_ids: list[SceneId]


class ArcPoint(DomainModel):
    scene_id: SceneId
    dramatic_function: DramaticFunction
    emotional_tone: list[str]


class StoryCharacter(DomainModel):
    character_id: CharacterId
    name: NonEmptyStr
    role: CharacterRole
    description: NonEmptyStr
    arc: list[ArcPoint]


class StoryRelationship(DomainModel):
    character_id: CharacterId
    related_character_id: CharacterId
    relation: NonEmptyStr
    note: str | None = None
    shared_scene_ids: list[SceneId]


class StoryEvent(DomainModel):
    event_id: NonEmptyStr
    scene_id: SceneId
    description: NonEmptyStr
    dramatic_function: DramaticFunction
    importance: Importance
    spoiler_level: SpoilerLevel
    key_dialogue_ids: list[DialogueId]


class EmotionalBeat(DomainModel):
    sequence: int
    scene_id: SceneId
    tones: list[str]


class StoryConflict(DomainModel):
    conflict_id: NonEmptyStr
    scene_id: SceneId
    character_ids: list[CharacterId]
    description: NonEmptyStr
    dialogue_ids: list[DialogueId]


class StoryStake(DomainModel):
    """What the story is fought over, taken from the high-importance props and where they appear."""

    prop_id: PropId
    description: NonEmptyStr
    scene_ids: list[SceneId]


class ProtectedReveal(DomainModel):
    scene_id: SceneId
    spoiler_level: SpoilerLevel
    dialogue_ids: list[DialogueId]


class TrailerHook(DomainModel):
    dialogue_id: DialogueId
    scene_id: SceneId
    speaker_id: CharacterId
    text: NonEmptyStr
    tone: NonEmptyStr
    spoiler_level: SpoilerLevel
    subtitle_safe: bool


class PerformanceSignal(DomainModel):
    audience: AudienceType
    campaign_id: CampaignId
    engagement_score: float


class SceneFunction(DomainModel):
    scene_id: SceneId
    sequence: int
    dramatic_function: DramaticFunction
    importance: Importance
    spoiler_level: SpoilerLevel
    emotional_tone: list[str]
    character_ids: list[CharacterId]
    performance: list[PerformanceSignal]


class StoryMap(DomainModel):
    episode_id: EpisodeId
    evidence_fingerprint: NonEmptyStr
    method: Literal["deterministic_baseline"]
    premise: Premise
    characters: list[StoryCharacter]
    relationships: list[StoryRelationship]
    events: list[StoryEvent]
    emotional_beats: list[EmotionalBeat]
    conflicts: list[StoryConflict]
    stakes: list[StoryStake]
    protected_reveals: list[ProtectedReveal]
    trailer_hooks: list[TrailerHook]
    scene_functions: list[SceneFunction]

    def protected_scene_ids(self) -> set[str]:
        return {reveal.scene_id for reveal in self.protected_reveals}

    def hook_dialogue_ids(self) -> set[str]:
        return {hook.dialogue_id for hook in self.trailer_hooks}
