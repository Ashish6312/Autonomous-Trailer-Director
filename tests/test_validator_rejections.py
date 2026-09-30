"""Each test breaks one rule in an otherwise valid dataset and checks the validator names it precisely."""

from support import assert_error, record

from trailer_director.data import validate_dataset


def test_duplicate_scene_id_fails(raw):
    duplicate = dict(record(raw["scenes"], "scene_id", "SC02"))
    raw["scenes"].append(duplicate)

    package, report = validate_dataset(raw)

    assert package is None
    assert_error(report, "scenes.SC02", "duplicate scene_id 'SC02'")


def test_unknown_dialogue_reference_fails(raw):
    record(raw["scenes"], "scene_id", "SC07")["dialogue_ids"].append("DLG_999")

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC07.dialogue_ids[3]", "unknown dialogue id 'DLG_999'")


def test_dialogue_listed_under_wrong_scene_fails(raw):
    record(raw["scenes"], "scene_id", "SC01")["dialogue_ids"].append("DLG_010")

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC01.dialogue_ids[4]", "belongs to scene 'SC02'")


def test_unknown_character_in_scene_fails(raw):
    record(raw["scenes"], "scene_id", "SC03")["characters"].append("CHAR_NOBODY")

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC03.characters[2]", "unknown character id 'CHAR_NOBODY'")


def test_unknown_speaker_fails(raw):
    record(raw["dialogue"], "dialogue_id", "DLG_001")["speaker_id"] = "CHAR_GHOST"

    _, report = validate_dataset(raw)

    assert_error(report, "dialogue.DLG_001.speaker_id", "unknown character id 'CHAR_GHOST'")


def test_speaker_absent_from_scene_fails(raw):
    record(raw["dialogue"], "dialogue_id", "DLG_011")["speaker_id"] = "CHAR_RAGHAV"

    _, report = validate_dataset(raw)

    assert_error(report, "dialogue.DLG_011.speaker_id", "not among scene SC03 characters")


def test_malformed_timecode_fails(raw):
    record(raw["scenes"], "scene_id", "SC04")["source_out"] = "00:61:00.000"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC04.source_out", "invalid timecode '00:61:00.000'")


def test_scene_ending_before_it_starts_fails(raw):
    scene = record(raw["scenes"], "scene_id", "SC04")
    scene["source_in"], scene["source_out"] = scene["source_out"], scene["source_in"]

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC04", "source_out must be after source_in")


def test_overlapping_scenes_fail(raw):
    record(raw["scenes"], "scene_id", "SC05")["source_in"] = "00:04:10.000"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC05.source_in", "overlaps previous scene SC04")


def test_dialogue_outside_scene_fails(raw):
    line = record(raw["dialogue"], "dialogue_id", "DLG_004")
    line["start"], line["end"] = "00:00:57.000", "00:01:01.000"

    _, report = validate_dataset(raw)

    assert_error(report, "dialogue.DLG_004", "falls outside scene SC01")


def test_unknown_music_reference_fails(raw):
    record(raw["scenes"], "scene_id", "SC06")["music_id"] = "MUS_99"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC06.music_id", "unknown music id 'MUS_99'")


def test_music_id_in_wrong_format_fails(raw):
    record(raw["scenes"], "scene_id", "SC06")["music_id"] = "MUSIC_03"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC06.music_id", "should match pattern")


def test_unknown_prop_fails(raw):
    record(raw["scenes"], "scene_id", "SC03")["props"].append(
        {"prop_id": "PROP_RING_01", "state": "introduced", "note": "Not in props.json."}
    )

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC03.props[0].prop_id", "unknown prop id 'PROP_RING_01'")


def test_prop_holder_must_be_in_scene(raw):
    letter = record(record(raw["scenes"], "scene_id", "SC04")["props"], "prop_id", "PROP_LETTER_01")
    letter["holder_id"] = "CHAR_KAMLA"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC04.props[2].holder_id", "'CHAR_KAMLA' is not among the scene's characters")


def test_prop_used_before_introduction_fails(raw):
    scene_two = record(raw["scenes"], "scene_id", "SC02")
    scene_two["props"] = [p for p in scene_two["props"] if p["prop_id"] != "PROP_LETTER_01"]

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC04.props[2].state", "'PROP_LETTER_01' first appears as 'held'")


def test_destroyed_prop_cannot_reappear(raw):
    lantern = record(record(raw["scenes"], "scene_id", "SC10")["props"], "prop_id", "PROP_LANTERN_01")
    lantern["state"] = "on_screen"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC10.props[2].state", "after being destroyed in SC09")


def test_scene_spoiler_level_must_cover_its_dialogue(raw):
    record(raw["scenes"], "scene_id", "SC10")["spoiler_level"] = "minor"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC10.spoiler_level", "lower than dialogue 'DLG_040' ('major')")


def test_scene_sensitivity_must_cover_its_dialogue(raw):
    record(raw["scenes"], "scene_id", "SC09")["sensitive_content"] = []

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC09.sensitive_content", "'strong_language' is 'none'")


def test_alias_cannot_name_two_identities(raw):
    arjun = record(raw["characters"], "character_id", "CHAR_ARJUN")
    arjun["aliases"].append({"alias": "Meera Sharma", "used_by": [], "note": "Invalid duplicate identity."})

    _, report = validate_dataset(raw)

    assert_error(report, "characters.CHAR_ARJUN.aliases[1]", "already identifies CHAR_MEERA")


def test_invalid_actor_rights_period_fails(raw):
    actor = record(raw["actor_rights"], "actor_id", "ACT_02")
    actor["valid_from"], actor["valid_until"] = "2027-12-31", "2026-08-01"

    _, report = validate_dataset(raw)

    assert_error(report, "actor_rights.ACT_02", "valid_until 2026-08-01 is before valid_from 2027-12-31")


def test_invalid_music_rights_date_fails(raw):
    record(raw["music"], "music_id", "MUS_03")["valid_until"] = "2026-02-30"

    _, report = validate_dataset(raw)

    assert_error(report, "music.MUS_03.valid_until", "(got '2026-02-30')")


def test_actor_rights_for_unknown_character_fails(raw):
    record(raw["actor_rights"], "actor_id", "ACT_05")["character_id"] = "CHAR_POSTMAN"

    _, report = validate_dataset(raw)

    assert_error(report, "actor_rights.ACT_05.character_id", "unknown character id 'CHAR_POSTMAN'")


def test_restriction_on_unknown_scene_fails(raw):
    record(raw["actor_rights"], "actor_id", "ACT_04")["restrictions"][0]["scene_ids"].append("SC42")

    _, report = validate_dataset(raw)

    assert_error(report, "actor_rights.ACT_04.restrictions[0].scene_ids[1]", "unknown scene id 'SC42'")


def test_invalid_audience_reference_fails(raw):
    record(raw["music"], "music_id", "MUS_02")["allowed_audiences"].append("teens")

    _, report = validate_dataset(raw)

    assert_error(report, "music.MUS_02.allowed_audiences[3]", "'family', 'young_adult' or 'dialect_region'")


def test_malformed_audience_profile_fails(raw):
    profile = record(raw["audience_profiles"], "audience", "young_adult")
    del profile["avoid"]
    profile["target_trailer_duration_seconds"] = "sixty"

    _, report = validate_dataset(raw)

    assert_error(report, "audience_profiles.young_adult.avoid", "Field required")
    assert_error(report, "audience_profiles.young_adult.target_trailer_duration_seconds", "valid integer")


def test_missing_audience_profile_fails(raw):
    raw["audience_profiles"] = [p for p in raw["audience_profiles"] if p["audience"] != "family"]

    _, report = validate_dataset(raw)

    assert_error(report, "audience_profiles", "no profile for audience 'family'")


def test_profile_evidence_must_cite_known_campaign(raw):
    record(raw["audience_profiles"], "audience", "family")["evidence"][0]["campaign_ids"] = ["CMP_IMAGINED"]

    _, report = validate_dataset(raw)

    assert_error(report, "audience_profiles.family.evidence[0].campaign_ids[0]", "unknown campaign id 'CMP_IMAGINED'")


def test_rating_policy_must_cover_every_category(raw):
    policy = record(raw["rating_policies"], "audience", "family")
    policy["content_rules"] = [rule for rule in policy["content_rules"] if rule["category"] != "violence"]

    _, report = validate_dataset(raw)

    assert_error(report, "rating_policies.family", "content_rules missing categories: violence")


def test_historical_record_for_unknown_scene_fails(raw):
    raw["historical_performance"][0]["scene_id"] = "SC13"

    _, report = validate_dataset(raw)

    assert_error(report, "historical_performance[CMP_PRELAUNCH_CLIPS/SC13/family].scene_id", "unknown scene id 'SC13'")


def test_cost_sheet_rejects_negative_costs(raw):
    raw["cost_sheet"]["unit_costs"][0]["unit_cost"] = -0.01

    _, report = validate_dataset(raw)

    assert_error(report, "cost_sheet.unit_costs[0].unit_cost", "greater than or equal to 0")


def test_non_object_record_is_reported_not_raised(raw):
    raw["dialogue"].append("DLG_050: stray line")

    _, report = validate_dataset(raw)

    assert_error(report, "dialogue[49]", "expected a JSON object, got string")


def test_unexpected_field_is_rejected(raw):
    record(raw["scenes"], "scene_id", "SC01")["musc_id"] = "MUS_01"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC01.musc_id", "Extra inputs are not permitted")


def test_schema_failure_is_not_repeated_as_unknown_reference(raw):
    record(raw["scenes"], "scene_id", "SC03")["time_of_day"] = "midnight"

    _, report = validate_dataset(raw)

    assert_error(report, "scenes.SC03.time_of_day", "(got 'midnight')")
    assert [issue.location for issue in report.errors] == ["scenes.SC03.time_of_day"]


def test_all_problems_are_reported_in_one_run(raw):
    record(raw["scenes"], "scene_id", "SC06")["music_id"] = "MUS_99"
    record(raw["dialogue"], "dialogue_id", "DLG_001")["speaker_id"] = "CHAR_GHOST"
    raw["historical_performance"][0]["scene_id"] = "SC13"

    _, report = validate_dataset(raw)

    assert len(report.errors) >= 3
