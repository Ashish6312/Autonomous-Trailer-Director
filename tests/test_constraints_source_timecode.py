import pytest
from pydantic import ValidationError
from support import clip, context, error_codes

from trailer_director.constraints import ConstraintSeverity, ReasonCode


def test_clip_from_existing_scene_with_contained_lines_passes(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:17.000", "00:02:28.000", ["DLG_011", "DLG_012"]), context())

    assert result.eligible
    assert result.errors == result.warnings == []


def test_nonexistent_scene_is_rejected_not_ignored(engine):
    result = engine.evaluate_scene("SC99", context())

    assert not result.eligible
    [violation] = result.violations
    assert (violation.rule_id, violation.reason_code, violation.entity_id) == (
        "SOURCE_SCENE_001",
        ReasonCode.SCENE_NOT_FOUND,
        "SC99",
    )


def test_clip_on_nonexistent_scene_reports_only_the_missing_scene(engine):
    result = engine.evaluate_clip(clip("SC99", "00:00:01.000", "00:00:05.000"), context())

    assert error_codes(result) == {(ReasonCode.SCENE_NOT_FOUND, "SC99")}


def test_missing_dialogue_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:17.000", "00:02:28.000", ["DLG_011", "DLG_999"]), context())

    assert (ReasonCode.DIALOGUE_NOT_FOUND, "DLG_999") in error_codes(result)


def test_dialogue_from_another_scene_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:17.000", "00:02:28.000", ["DLG_040"]), context())

    assert (ReasonCode.DIALOGUE_SCENE_MISMATCH, "DLG_040") in error_codes(result)


def test_missing_music_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:17.000", "00:02:28.000", music_id="MUS_99"), context())

    assert (ReasonCode.MUSIC_NOT_FOUND, "MUS_99") in error_codes(result)
    assert engine.evaluate_music("MUS_99", context()).reason_codes == {ReasonCode.MUSIC_NOT_FOUND}


def test_clip_starting_before_scene_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:05.000", "00:02:16.000"), context())

    [violation] = result.errors
    assert violation.reason_code is ReasonCode.CLIP_STARTS_BEFORE_SCENE
    assert violation.details == {"scene_in": "00:02:10.000", "scene_out": "00:03:18.000"}


def test_clip_ending_after_scene_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC03", "00:03:10.000", "00:03:25.000"), context())

    assert {v.reason_code for v in result.errors} == {ReasonCode.CLIP_ENDS_AFTER_SCENE}


@pytest.mark.parametrize(
    ("source_in", "source_out"), [("00:02:20.000", "00:02:20.000"), ("00:02:25.000", "00:02:20.000")]
)
def test_clip_must_start_before_it_ends(source_in, source_out):
    with pytest.raises(ValidationError, match="source_out must be after source_in"):
        clip("SC03", source_in, source_out)


def test_clip_timecodes_must_be_well_formed():
    with pytest.raises(ValidationError, match="invalid timecode"):
        clip("SC03", "2:17", "00:02:28.000")


def test_declared_line_outside_clip_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:17.000", "00:02:22.500", ["DLG_011", "DLG_012"]), context())

    assert error_codes(result) == {(ReasonCode.DIALOGUE_OUTSIDE_CLIP, "DLG_012")}


def test_undeclared_audible_line_is_a_warning(engine):
    result = engine.evaluate_clip(clip("SC03", "00:02:17.000", "00:02:28.000", ["DLG_011"]), context())

    assert result.eligible
    [warning] = result.warnings
    assert (warning.reason_code, warning.entity_id) == (ReasonCode.UNDECLARED_DIALOGUE_IN_CLIP, "DLG_012")
    assert warning.severity is ConstraintSeverity.WARNING
