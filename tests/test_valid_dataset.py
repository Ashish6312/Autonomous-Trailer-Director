import pytest
from support import DATA_DIR

from trailer_director.data import load_episode_package, validate_dataset
from trailer_director.domain import AudienceType, SpoilerLevel
from trailer_director.errors import UnknownEntityError

# A known data finding, kept on purpose: fixing it would change the evidence fingerprint that the recorded
# live run (runs/planner_runs) was made against. See README, Known limitations.
KNOWN_WARNINGS = [("characters.CHAR_BANSI.relationships[4]", "CHAR_SHANKAR does not list CHAR_BANSI back")]


def test_shipped_dataset_has_no_errors_and_only_the_known_warning(raw):
    package, report = validate_dataset(raw)

    assert report.errors == [], report.format()
    assert [(i.location, i.message.split(";")[0]) for i in report.warnings] == KNOWN_WARNINGS
    assert package is not None


def test_shipped_dataset_has_the_expected_size():
    package = load_episode_package(DATA_DIR)

    assert len(package.scenes) == 12
    assert 35 <= len(package.dialogue) <= 50
    assert len(package.music) == 5
    assert set(package.rating_policies) == set(AudienceType)
    assert set(package.audience_profiles) == set(AudienceType)


def test_package_lookups_resolve_across_files():
    package = load_episode_package(DATA_DIR)

    reveal = package.scene("SC10")
    lines = package.lines_for_scene("SC10")

    assert reveal.spoiler_level is SpoilerLevel.MAJOR
    assert [line.dialogue_id for line in lines] == list(reveal.dialogue_ids)
    assert package.music_asset(reveal.music_id).allowed_for_promotion is False
    assert [scene.sequence for scene in package.scenes_in_order()] == list(range(1, 13))


def test_lookup_of_invented_id_raises():
    package = load_episode_package(DATA_DIR)

    with pytest.raises(UnknownEntityError, match="unknown scene id 'SC99'"):
        package.scene("SC99")
    with pytest.raises(UnknownEntityError):
        package.performance_for_scene("SC99")


def test_timecodes_round_trip_to_source_format(pristine_raw):
    package = load_episode_package(DATA_DIR)
    source_scene = next(scene for scene in pristine_raw["scenes"] if scene["scene_id"] == "SC10")

    dumped = package.scene("SC10").model_dump(mode="json")

    assert dumped["source_in"] == source_scene["source_in"]
    assert dumped["source_out"] == source_scene["source_out"]
