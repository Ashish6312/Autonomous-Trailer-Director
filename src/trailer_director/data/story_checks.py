"""Referential, timeline and continuity checks for editorial evidence."""

from collections import defaultdict
from itertools import pairwise

from trailer_director.data.parsing import ParsedDataset
from trailer_director.data.report import ValidationReport
from trailer_director.domain import (
    Character,
    ContentSeverity,
    Dialogue,
    PropAppearance,
    PropState,
    Scene,
    SensitiveCategory,
    SensitiveContent,
)
from trailer_director.domain.relations import RECIPROCAL_KIND, RELATION_KINDS
from trailer_director.domain.timecode import format_timecode


def check_story(parsed: ParsedDataset, report: ValidationReport) -> None:
    _check_episode(parsed, report)
    _check_characters(parsed, report)
    _check_scene_references(parsed, report)
    _check_scene_timeline(parsed, report)
    _check_dialogue(parsed, report)
    _check_prop_continuity(parsed, report)


def _check_episode(parsed: ParsedDataset, report: ValidationReport) -> None:
    episode = parsed.episode
    if episode is None:
        return

    for index, scene_id in enumerate(episode.scene_ids):
        parsed.resolves("scenes", scene_id, f"episode.scene_ids[{index}]", report)
    for scene_id in parsed.scenes:
        if scene_id not in episode.scene_ids:
            report.error(f"scenes.{scene_id}", "scene is not listed in episode.scene_ids")

    listed = [scene_id for scene_id in episode.scene_ids if scene_id in parsed.scenes]
    by_sequence = [scene.scene_id for scene in parsed.scenes_in_order() if scene.scene_id in listed]
    if listed != by_sequence:
        report.error("episode.scene_ids", "order does not match scene sequence numbers")

    for index, character_id in enumerate(episode.character_ids):
        parsed.resolves("characters", character_id, f"episode.character_ids[{index}]", report)
    for character_id in parsed.characters:
        if character_id not in episode.character_ids:
            report.error(f"characters.{character_id}", "character is not listed in episode.character_ids")

    for index, music_id in enumerate(episode.music_ids):
        parsed.resolves("music", music_id, f"episode.music_ids[{index}]", report)

    if parsed.scenes:
        last = max(parsed.scenes.values(), key=lambda scene: scene.source_out)
        episode_end_ms = episode.duration_seconds * 1000
        if last.source_out > episode_end_ms:
            report.error(
                "episode.duration_seconds",
                f"episode ends at {format_timecode(episode_end_ms)} but scene {last.scene_id} "
                f"runs until {format_timecode(last.source_out)}",
            )


def _check_characters(parsed: ParsedDataset, report: ValidationReport) -> None:
    identity_owner = {character.name.casefold(): character.character_id for character in parsed.characters.values()}

    for character in parsed.characters.values():
        location = f"characters.{character.character_id}"
        for index, relationship in enumerate(character.relationships):
            target = relationship.character_id
            if target == character.character_id:
                report.error(f"{location}.relationships[{index}]", "character cannot be related to itself")
            else:
                parsed.resolves("characters", target, f"{location}.relationships[{index}].character_id", report)

        _check_reciprocal_relationships(character, parsed, report)

        for index, alias in enumerate(character.aliases):
            owner = identity_owner.setdefault(alias.alias.casefold(), character.character_id)
            if owner != character.character_id:
                report.error(
                    f"{location}.aliases[{index}]",
                    f"alias '{alias.alias}' already identifies {owner}; one name must map to one identity",
                )
            for user_index, user_id in enumerate(alias.used_by):
                parsed.resolves("characters", user_id, f"{location}.aliases[{index}].used_by[{user_index}]", report)


def _check_reciprocal_relationships(character: Character, parsed: ParsedDataset, report: ValidationReport) -> None:
    """A relationship should be recorded from both sides, with matching kinds (mother <-> daughter, not niece)."""
    for index, relationship in enumerate(character.relationships):
        other = parsed.characters.get(relationship.character_id)
        if other is None or other.character_id == character.character_id:
            continue
        location = f"characters.{character.character_id}.relationships[{index}]"
        back = next((r for r in other.relationships if r.character_id == character.character_id), None)
        if back is None:
            report.warning(
                location,
                f"{other.character_id} does not list {character.character_id} back; relationships should be "
                "recorded from both sides",
            )
            continue
        kind, back_kind = RELATION_KINDS.get(relationship.relation), RELATION_KINDS.get(back.relation)
        if kind and back_kind and RECIPROCAL_KIND[kind] != back_kind:
            report.warning(
                location,
                f"'{relationship.relation}' of {other.character_id} does not match its reverse "
                f"'{back.relation}' of {character.character_id}",
            )


def _check_scene_references(parsed: ParsedDataset, report: ValidationReport) -> None:
    episode_music = set(parsed.episode.music_ids) if parsed.episode else set()

    for scene in parsed.scenes.values():
        location = f"scenes.{scene.scene_id}"
        parsed.resolves("locations", scene.location_id, f"{location}.location_id", report)
        for index, character_id in enumerate(scene.characters):
            parsed.resolves("characters", character_id, f"{location}.characters[{index}]", report)

        music_known = scene.music_id is not None and parsed.resolves(
            "music", scene.music_id, f"{location}.music_id", report
        )
        if music_known and parsed.episode and scene.music_id not in episode_music:
            report.error(f"{location}.music_id", f"music '{scene.music_id}' is not listed in episode.music_ids")

        _check_scene_dialogue(scene, parsed, report)
        for index, appearance in enumerate(scene.props):
            _check_prop_appearance(scene, index, appearance, parsed, report)


def _check_scene_dialogue(scene: Scene, parsed: ParsedDataset, report: ValidationReport) -> None:
    """A scene's spoiler and sensitivity flags must cover every line in it,
    otherwise filtering at scene level would leak line-level problems."""
    location = f"scenes.{scene.scene_id}"
    scene_severity = _max_severity_by_category(scene.sensitive_content)

    for index, dialogue_id in enumerate(scene.dialogue_ids):
        if not parsed.resolves("dialogue", dialogue_id, f"{location}.dialogue_ids[{index}]", report):
            continue
        line = parsed.dialogue[dialogue_id]
        if line.scene_id != scene.scene_id:
            report.error(
                f"{location}.dialogue_ids[{index}]", f"dialogue '{dialogue_id}' belongs to scene '{line.scene_id}'"
            )
            continue
        if line.spoiler_level.rank > scene.spoiler_level.rank:
            report.error(
                f"{location}.spoiler_level",
                f"'{scene.spoiler_level}' is lower than dialogue '{dialogue_id}' ('{line.spoiler_level}')",
            )
        for flag in line.sensitive_content:
            scene_level = scene_severity.get(flag.category, ContentSeverity.NONE)
            if flag.severity.rank > scene_level.rank:
                report.error(
                    f"{location}.sensitive_content",
                    f"'{flag.category}' is '{scene_level}' but dialogue '{dialogue_id}' is '{flag.severity}'",
                )


def _check_prop_appearance(
    scene: Scene, index: int, appearance: PropAppearance, parsed: ParsedDataset, report: ValidationReport
) -> None:
    location = f"scenes.{scene.scene_id}.props[{index}]"
    parsed.resolves("props", appearance.prop_id, f"{location}.prop_id", report)
    if appearance.holder_id is not None and appearance.holder_id not in scene.characters:
        report.error(f"{location}.holder_id", f"holder '{appearance.holder_id}' is not among the scene's characters")
    if appearance.state in (PropState.HELD, PropState.REVEALED) and appearance.holder_id is None:
        report.error(f"{location}.holder_id", f"state '{appearance.state}' requires a holder_id")


def _check_scene_timeline(parsed: ParsedDataset, report: ValidationReport) -> None:
    ordered = parsed.scenes_in_order()

    sequence_owner: dict[int, str] = {}
    for scene in ordered:
        owner = sequence_owner.setdefault(scene.sequence, scene.scene_id)
        if owner != scene.scene_id:
            report.error(f"scenes.{scene.scene_id}.sequence", f"sequence {scene.sequence} is also used by {owner}")
    all_scenes_parsed = not parsed.rejected_ids["scenes"]
    if all_scenes_parsed and sorted(sequence_owner) != list(range(1, len(sequence_owner) + 1)):
        report.error("scenes", "sequence numbers must run from 1 without gaps")

    for previous, current in pairwise(ordered):
        if current.source_in < previous.source_out:
            report.error(
                f"scenes.{current.scene_id}.source_in",
                f"{format_timecode(current.source_in)} overlaps previous scene {previous.scene_id} "
                f"ending at {format_timecode(previous.source_out)}",
            )


def _check_dialogue(parsed: ParsedDataset, report: ValidationReport) -> None:
    lines_by_scene: dict[str, list[Dialogue]] = defaultdict(list)

    for line in parsed.dialogue.values():
        location = f"dialogue.{line.dialogue_id}"
        speaker_known = parsed.resolves("characters", line.speaker_id, f"{location}.speaker_id", report)
        if not parsed.resolves("scenes", line.scene_id, f"{location}.scene_id", report):
            continue
        scene = parsed.scenes[line.scene_id]
        lines_by_scene[scene.scene_id].append(line)

        if speaker_known and line.speaker_id not in scene.characters:
            report.error(
                f"{location}.speaker_id",
                f"speaker '{line.speaker_id}' is not among scene {scene.scene_id} characters",
            )
        if line.start < scene.source_in or line.end > scene.source_out:
            report.error(
                location,
                f"{format_timecode(line.start)}-{format_timecode(line.end)} falls outside scene "
                f"{scene.scene_id} ({format_timecode(scene.source_in)}-{format_timecode(scene.source_out)})",
            )
        if line.dialogue_id not in scene.dialogue_ids:
            report.error(f"{location}.scene_id", f"line is not listed in scenes.{scene.scene_id}.dialogue_ids")

    for lines in lines_by_scene.values():
        for previous, current in pairwise(sorted(lines, key=lambda line: line.start)):
            if current.start < previous.end:
                report.warning(
                    f"dialogue.{current.dialogue_id}.start",
                    f"overlaps dialogue {previous.dialogue_id}; check subtitle timing",
                )


def _check_prop_continuity(parsed: ParsedDataset, report: ValidationReport) -> None:
    """Props must be introduced before any other use and never reappear after being destroyed."""
    appearances: dict[str, list[tuple[str, int, PropAppearance]]] = defaultdict(list)
    for scene in parsed.scenes_in_order():
        for index, appearance in enumerate(scene.props):
            appearances[appearance.prop_id].append((scene.scene_id, index, appearance))

    for prop_id in parsed.props:
        if prop_id not in appearances:
            report.warning(f"props.{prop_id}", "prop never appears in any scene")

    for prop_id, timeline in appearances.items():
        if prop_id not in parsed.props:
            continue
        destroyed_in: str | None = None
        for position, (scene_id, index, appearance) in enumerate(timeline):
            location = f"scenes.{scene_id}.props[{index}].state"
            if position == 0 and appearance.state is not PropState.INTRODUCED:
                report.error(location, f"'{prop_id}' first appears as '{appearance.state}', expected 'introduced'")
            elif position > 0 and appearance.state is PropState.INTRODUCED:
                report.error(location, f"'{prop_id}' is introduced again after scene {timeline[0][0]}")
            if destroyed_in is not None and appearance.state is not PropState.REFERENCED:
                report.error(location, f"'{prop_id}' is '{appearance.state}' after being destroyed in {destroyed_in}")
            if appearance.state is PropState.DESTROYED:
                destroyed_in = scene_id


def _max_severity_by_category(flags: list[SensitiveContent]) -> dict[SensitiveCategory, ContentSeverity]:
    result: dict[SensitiveCategory, ContentSeverity] = {}
    for flag in flags:
        current = result.get(flag.category, ContentSeverity.NONE)
        if flag.severity.rank > current.rank:
            result[flag.category] = flag.severity
    return result
