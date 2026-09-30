"""Schema phase: turn raw JSON into typed records, reporting every failure.

Records that fail are skipped rather than aborting the run, so one bad
record does not hide problems elsewhere in the dataset.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from trailer_director.data.report import ValidationReport
from trailer_director.domain import (
    ActorRights,
    AudienceProfile,
    Character,
    CostSheet,
    Dialogue,
    Episode,
    EpisodePackage,
    HistoricalPerformance,
    Location,
    MusicAsset,
    Prop,
    RatingPolicy,
    Scene,
)

_PYDANTIC_MESSAGE_PREFIXES = ("Value error, ", "Assertion failed, ")
_ENTITY_KIND = {
    "characters": "character",
    "locations": "location",
    "props": "prop",
    "scenes": "scene",
    "dialogue": "dialogue",
    "music": "music",
}


@dataclass
class ParsedDataset:
    episode: Episode | None = None
    cost_sheet: CostSheet | None = None
    characters: dict[str, Character] = field(default_factory=dict)
    locations: dict[str, Location] = field(default_factory=dict)
    props: dict[str, Prop] = field(default_factory=dict)
    scenes: dict[str, Scene] = field(default_factory=dict)
    dialogue: dict[str, Dialogue] = field(default_factory=dict)
    actor_rights: dict[str, ActorRights] = field(default_factory=dict)
    music: dict[str, MusicAsset] = field(default_factory=dict)
    rating_policies: dict[str, RatingPolicy] = field(default_factory=dict)
    audience_profiles: dict[str, AudienceProfile] = field(default_factory=dict)
    historical_performance: list[HistoricalPerformance] = field(default_factory=list)
    rejected_ids: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def resolves(self, collection: str, entity_id: str, location: str, report: ValidationReport) -> bool:
        """Return whether ``entity_id`` names a parsed record, reporting it if it is unknown.

        IDs whose record exists but failed schema validation are not reported
        again: the schema error is the root cause.
        """
        if entity_id in getattr(self, collection):
            return True
        if entity_id not in self.rejected_ids[collection]:
            report.error(location, f"unknown {_ENTITY_KIND[collection]} id '{entity_id}'")
        return False

    def scenes_in_order(self) -> list[Scene]:
        return sorted(self.scenes.values(), key=lambda scene: scene.sequence)

    def to_package(self) -> EpisodePackage:
        if self.episode is None or self.cost_sheet is None:
            raise ValueError("cannot build a package without an episode and a cost sheet")
        return EpisodePackage(
            episode=self.episode,
            characters=self.characters,
            locations=self.locations,
            props=self.props,
            scenes=self.scenes,
            dialogue=self.dialogue,
            actor_rights=self.actor_rights,
            music=self.music,
            rating_policies=self.rating_policies,
            audience_profiles=self.audience_profiles,
            historical_performance=tuple(self.historical_performance),
            cost_sheet=self.cost_sheet,
        )


def parse_dataset(raw: Mapping[str, Any], report: ValidationReport) -> ParsedDataset:
    rejected_ids: defaultdict[str, set[str]] = defaultdict(set)

    def collection[M: BaseModel](name: str, model: type[M], id_field: str | None) -> list[M]:
        return _parse_collection(raw.get(name), name, model, id_field, report, rejected_ids[name])

    return ParsedDataset(
        episode=_parse_single(raw.get("episode"), "episode", Episode, report),
        cost_sheet=_parse_single(raw.get("cost_sheet"), "cost_sheet", CostSheet, report),
        characters={c.character_id: c for c in collection("characters", Character, "character_id")},
        locations={loc.location_id: loc for loc in collection("locations", Location, "location_id")},
        props={p.prop_id: p for p in collection("props", Prop, "prop_id")},
        scenes={s.scene_id: s for s in collection("scenes", Scene, "scene_id")},
        dialogue={d.dialogue_id: d for d in collection("dialogue", Dialogue, "dialogue_id")},
        actor_rights={a.actor_id: a for a in collection("actor_rights", ActorRights, "actor_id")},
        music={m.music_id: m for m in collection("music", MusicAsset, "music_id")},
        rating_policies={p.audience: p for p in collection("rating_policies", RatingPolicy, "audience")},
        audience_profiles={p.audience: p for p in collection("audience_profiles", AudienceProfile, "audience")},
        historical_performance=collection("historical_performance", HistoricalPerformance, None),
        rejected_ids=rejected_ids,
    )


def _parse_single[M: BaseModel](data: Any, name: str, model: type[M], report: ValidationReport) -> M | None:
    if not isinstance(data, dict):
        report.error(name, f"expected a JSON object, got {_json_type(data)}")
        return None
    return _validate_record(model, data, name, report)


def _parse_collection[M: BaseModel](
    records: Any,
    name: str,
    model: type[M],
    id_field: str | None,
    report: ValidationReport,
    rejected_ids: set[str],
) -> list[M]:
    if not isinstance(records, list):
        report.error(name, f"expected a JSON array of records, got {_json_type(records)}")
        return []

    parsed: list[M] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records):
        record_id = record.get(id_field) if id_field and isinstance(record, dict) else None
        label = f"{name}.{record_id}" if isinstance(record_id, str) and record_id else f"{name}[{index}]"

        if not isinstance(record, dict):
            report.error(label, f"expected a JSON object, got {_json_type(record)}")
            continue
        if isinstance(record_id, str):
            if record_id in seen_ids:
                report.error(label, f"duplicate {id_field} '{record_id}'")
                continue
            seen_ids.add(record_id)

        model_instance = _validate_record(model, record, label, report)
        if model_instance is not None:
            parsed.append(model_instance)
        elif isinstance(record_id, str):
            rejected_ids.add(record_id)
    return parsed


def _validate_record[M: BaseModel](
    model: type[M], data: dict[str, Any], label: str, report: ValidationReport
) -> M | None:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            report.error(label + _format_loc(error["loc"]), _format_message(error))
        return None


def _format_loc(loc: tuple[int | str, ...]) -> str:
    return "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in loc)


def _format_message(error: Mapping[str, Any]) -> str:
    message: str = error["msg"]
    for prefix in _PYDANTIC_MESSAGE_PREFIXES:
        message = message.removeprefix(prefix)
    value = error.get("input")
    if error["type"] != "missing" and isinstance(value, str | int | float) and not isinstance(value, bool):
        message = f"{message} (got {value!r})"
    return message


def _json_type(value: Any) -> str:
    match value:
        case None:
            return "null"
        case dict():
            return "object"
        case list():
            return "array"
        case bool():
            return "boolean"
        case str():
            return "string"
        case _:
            return "number"
