"""Checks that every evidence ID inside a story map resolves against the evidence package."""

from collections.abc import Iterator
from typing import Any

from trailer_director.domain import EpisodePackage
from trailer_director.story.models import StoryMap

_REFERENCE_KEYS = frozenset(
    {
        "scene_id",
        "scene_ids",
        "shared_scene_ids",
        "dialogue_id",
        "dialogue_ids",
        "key_dialogue_ids",
        "character_id",
        "character_ids",
        "related_character_id",
        "speaker_id",
        "prop_id",
        "campaign_id",
    }
)


def unresolved_references(story_map: StoryMap, evidence: EpisodePackage) -> list[tuple[str, str]]:
    """(location, ID) for every reference in the story map that does not exist in the evidence."""
    return [
        (location, entity_id)
        for location, entity_id in _references(story_map.model_dump(mode="json"), "story_map")
        if not evidence.has_entity(entity_id)
    ]


def _references(value: Any, location: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{location}.{key}"
            if key in _REFERENCE_KEYS:
                ids = item if isinstance(item, list) else [item]
                yield from ((path, entity_id) for entity_id in ids if isinstance(entity_id, str))
            else:
                yield from _references(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _references(item, f"{location}[{index}]")
