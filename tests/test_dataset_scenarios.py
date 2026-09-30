"""Guards the deliberate test conditions planted in the dataset.

If someone edits the data and removes one of these conditions, the later
agent tests would silently lose coverage; these tests fail first.
"""

import pytest
from support import DATA_DIR

from trailer_director.data import load_episode_package
from trailer_director.domain import AudienceType, PropState, SpoilerLevel


@pytest.fixture(scope="module")
def package():
    return load_episode_package(DATA_DIR)


@pytest.mark.parametrize("audience", list(AudienceType))
def test_best_performing_clip_exceeds_audience_spoiler_limit(package, audience):
    records = [r for r in package.historical_performance if r.audience is audience]
    best = max(records, key=lambda r: r.engagement_score)

    scene = package.scene(best.scene_id)
    limit = package.rating_policy(audience).max_spoiler_level

    assert scene.spoiler_level.rank > limit.rank


def test_a_licensed_track_expires_before_release(package):
    release = package.episode.release_date
    expiring = [m for m in package.music.values() if m.allowed_for_promotion and not m.is_active_on(release)]

    assert [m.music_id for m in expiring] == ["MUS_03"]


def test_production_note_attempts_contract_override_and_stays_inert_data(package):
    scene = package.scene("SC09")
    restricted_scenes = {
        scene_id
        for rights in package.actor_rights.values()
        for restriction in rights.restrictions
        for scene_id in restriction.scene_ids
    }

    assert "ignore contract restrictions" in scene.production_notes.lower()
    assert "SC09" in restricted_scenes
    assert "young_adult" in package.music_asset("MUS_03").allowed_audiences
    assert AudienceType.FAMILY not in package.music_asset("MUS_03").allowed_audiences


def test_missing_letter_follows_planned_continuity(package):
    timeline = [
        (scene.scene_id, appearance.state, appearance.holder_id)
        for scene in package.scenes_in_order()
        for appearance in scene.props
        if appearance.prop_id == "PROP_LETTER_01"
    ]

    assert timeline == [
        ("SC02", PropState.INTRODUCED, None),
        ("SC04", PropState.HELD, "CHAR_RAGHAV"),
        ("SC07", PropState.REFERENCED, None),
        ("SC08", PropState.REFERENCED, None),
        ("SC10", PropState.REVEALED, "CHAR_ARJUN"),
        ("SC11", PropState.HELD, "CHAR_MEERA"),
    ]


def test_honorific_kaka_is_not_modelled_as_kinship(package):
    bansi = package.character("CHAR_BANSI")
    meera = package.character("CHAR_MEERA")

    kaka_alias = next(alias for alias in bansi.aliases if "Kaka" in alias.alias)
    relation_to_bansi = next(r.relation for r in meera.relationships if r.character_id == "CHAR_BANSI")

    assert "not related" in kaka_alias.note.lower()
    assert relation_to_bansi == "family_acquaintance"


def test_major_spoiler_is_confined_to_the_reveal_scene(package):
    major_lines = {line.scene_id for line in package.dialogue.values() if line.spoiler_level is SpoilerLevel.MAJOR}

    assert major_lines == {"SC10"}
