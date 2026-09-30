import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from pydantic import BaseModel

from trailer_director.domain.audience import AudienceProfile, RatingPolicy
from trailer_director.domain.economics import CostSheet
from trailer_director.domain.enums import AudienceType
from trailer_director.domain.performance import HistoricalPerformance
from trailer_director.domain.rights import ActorRights, MusicAsset
from trailer_director.domain.story import Character, Dialogue, Episode, Location, Prop, Scene
from trailer_director.errors import UnknownEntityError

_FINGERPRINT_LENGTH = 16


def _lookup[T](collection: Mapping[str, T], kind: str, entity_id: str) -> T:
    try:
        return collection[entity_id]
    except KeyError:
        raise UnknownEntityError(kind, entity_id) from None


@dataclass(frozen=True)
class EpisodePackage:
    """The validated source of truth for one episode.

    Only constructed after the dataset validator reports no errors, so every
    cross-reference inside it is known to resolve. Lookups raise
    ``UnknownEntityError`` rather than returning ``None``, so callers cannot
    silently proceed with an invented ID.
    """

    episode: Episode
    characters: Mapping[str, Character]
    locations: Mapping[str, Location]
    props: Mapping[str, Prop]
    scenes: Mapping[str, Scene]
    dialogue: Mapping[str, Dialogue]
    actor_rights: Mapping[str, ActorRights]
    music: Mapping[str, MusicAsset]
    rating_policies: Mapping[AudienceType, RatingPolicy]
    audience_profiles: Mapping[AudienceType, AudienceProfile]
    historical_performance: tuple[HistoricalPerformance, ...]
    cost_sheet: CostSheet

    def scene(self, scene_id: str) -> Scene:
        return _lookup(self.scenes, "scene", scene_id)

    def dialogue_line(self, dialogue_id: str) -> Dialogue:
        return _lookup(self.dialogue, "dialogue", dialogue_id)

    def character(self, character_id: str) -> Character:
        return _lookup(self.characters, "character", character_id)

    def music_asset(self, music_id: str) -> MusicAsset:
        return _lookup(self.music, "music", music_id)

    def rating_policy(self, audience: AudienceType) -> RatingPolicy:
        return _lookup(self.rating_policies, "rating policy", audience)

    def audience_profile(self, audience: AudienceType) -> AudienceProfile:
        return _lookup(self.audience_profiles, "audience profile", audience)

    def has_entity(self, entity_id: str) -> bool:
        """Whether any evidence record has this ID; the ID prefix says which collection to look in."""
        if entity_id.startswith("CMP_"):
            return any(record.campaign_id == entity_id for record in self.historical_performance)
        collections: dict[str, Mapping[str, Any]] = {
            "SC": self.scenes,
            "DLG_": self.dialogue,
            "CHAR_": self.characters,
            "LOC_": self.locations,
            "PROP_": self.props,
            "MUS_": self.music,
            "ACT_": self.actor_rights,
        }
        return any(entity_id.startswith(prefix) and entity_id in records for prefix, records in collections.items())

    def fingerprint(self) -> str:
        """Short content hash of all evidence, so derived artefacts can name the evidence they came from."""
        content = {field.name: _jsonable(getattr(self, field.name)) for field in fields(self)}
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:_FINGERPRINT_LENGTH]

    def find_actor_rights(self, character_id: str) -> ActorRights | None:
        """Rights record for the performer playing ``character_id``, or ``None`` if there is none."""
        return next((r for r in self.actor_rights.values() if r.character_id == character_id), None)

    def scenes_in_order(self) -> list[Scene]:
        return sorted(self.scenes.values(), key=lambda scene: scene.sequence)

    def lines_for_scene(self, scene_id: str) -> list[Dialogue]:
        return [self.dialogue_line(dialogue_id) for dialogue_id in self.scene(scene_id).dialogue_ids]

    def performance_for_scene(self, scene_id: str) -> list[HistoricalPerformance]:
        self.scene(scene_id)
        return [record for record in self.historical_performance if record.scene_id == scene_id]


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value
