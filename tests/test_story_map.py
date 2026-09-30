from trailer_director.domain import SpoilerLevel
from trailer_director.story import build_story_map, unresolved_references
from trailer_director.story.mapper import HOOK_SPOILER_CEILING


def test_story_map_is_built_from_the_dataset(story_map, evidence):
    assert story_map.episode_id == evidence.episode.episode_id
    assert story_map.method == "deterministic_baseline"
    assert story_map.evidence_fingerprint == evidence.fingerprint()
    assert story_map.premise.logline == evidence.episode.spoiler_safe_logline
    assert [event.scene_id for event in story_map.events] == [s.scene_id for s in evidence.scenes_in_order()]
    assert {c.character_id for c in story_map.characters} == set(evidence.episode.character_ids)


def test_every_reference_resolves(story_map, evidence):
    assert unresolved_references(story_map, evidence) == []


def test_invented_ids_are_detected(story_map, evidence):
    hook = story_map.trailer_hooks[0].model_copy(update={"scene_id": "SC99"})
    tampered = story_map.model_copy(update={"trailer_hooks": [hook, *story_map.trailer_hooks[1:]]})

    assert unresolved_references(tampered, evidence) == [("story_map.trailer_hooks[0].scene_id", "SC99")]


def test_protected_reveals_are_retained(story_map):
    reveals = {reveal.scene_id: reveal for reveal in story_map.protected_reveals}

    assert reveals["SC10"].spoiler_level is SpoilerLevel.MAJOR
    assert reveals["SC10"].dialogue_ids == ["DLG_039", "DLG_040", "DLG_041", "DLG_042", "DLG_043"]
    assert "SC10" in story_map.protected_scene_ids()


def test_trailer_hooks_never_include_protected_lines(story_map):
    protected_lines = {d for reveal in story_map.protected_reveals for d in reveal.dialogue_ids}

    assert story_map.trailer_hooks
    assert all(hook.spoiler_level.rank <= HOOK_SPOILER_CEILING.rank for hook in story_map.trailer_hooks)
    assert not story_map.hook_dialogue_ids() & protected_lines


def test_structure_carries_source_references(story_map):
    letter = next(stake for stake in story_map.stakes if stake.prop_id == "PROP_LETTER_01")
    meera = next(c for c in story_map.characters if c.character_id == "CHAR_MEERA")
    siblings = next(
        r for r in story_map.relationships if (r.character_id, r.related_character_id) == ("CHAR_ARJUN", "CHAR_MEERA")
    )

    assert letter.scene_ids == ["SC02", "SC04", "SC07", "SC08", "SC10", "SC11"]
    assert [point.scene_id for point in meera.arc][:3] == ["SC01", "SC02", "SC03"]
    assert {"SC05", "SC06", "SC11"} <= set(siblings.shared_scene_ids)
    assert [c.scene_id for c in story_map.conflicts] == ["SC05", "SC08", "SC09"]


def test_story_map_is_deterministic(evidence):
    assert build_story_map(evidence) == build_story_map(evidence)
