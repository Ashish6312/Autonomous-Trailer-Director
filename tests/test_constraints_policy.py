"""Spoiler, rating, misleading-context and trailer-length rules."""

from dataclasses import replace

from support import clip, context, error_codes

from trailer_director.constraints import ConstraintEngine, ReasonCode
from trailer_director.domain import AudienceType, SpoilerLevel
from trailer_director.domain.edit import TrailerCandidate


def test_spoiler_within_audience_ceiling_is_allowed(engine):
    result = engine.evaluate_scene("SC11", context("young_adult"))

    assert result.eligible


def test_spoiler_above_audience_ceiling_is_rejected(engine):
    result = engine.evaluate_scene("SC11", context("family"))

    assert (ReasonCode.SPOILER_LEVEL_EXCEEDED, "SC11") in error_codes(result)
    assert (ReasonCode.SPOILER_LEVEL_EXCEEDED, "DLG_044") in error_codes(result)


def test_high_performing_major_spoiler_is_still_rejected_for_family(engine, evidence):
    family_records = [r for r in evidence.performance_for_scene("SC10") if r.audience is AudienceType.FAMILY]
    assert evidence.scene("SC10").spoiler_level is SpoilerLevel.MAJOR
    assert max(r.engagement_score for r in family_records) >= 85

    result = engine.evaluate_scene("SC10", context("family"))

    assert not result.eligible
    spoiler = next(v for v in result.errors if v.entity_id == "SC10")
    assert spoiler.details == {"spoiler_level": "major", "max_spoiler_level": "minor", "audience": "family"}
    assert ReasonCode.HISTORICAL_PERFORMANCE in result.reason_codes


def test_historical_performance_never_changes_the_verdict(engine, evidence):
    without_history = ConstraintEngine(replace(evidence, historical_performance=()))

    for scene_id in evidence.scenes:
        for audience in AudienceType:
            with_notes = engine.evaluate_scene(scene_id, context(audience.value))
            without_notes = without_history.evaluate_scene(scene_id, context(audience.value))
            assert with_notes.errors == without_notes.errors
            assert with_notes.warnings == without_notes.warnings
            assert with_notes.eligible == without_notes.eligible


def test_content_within_policy_is_allowed(engine):
    result = engine.evaluate_scene("SC02", context("family"))

    assert result.eligible


def test_prohibited_category_is_named(engine):
    result = engine.evaluate_clip(clip("SC09", "00:09:34.000", "00:09:39.000", ["DLG_038"]), context("family"))

    violence = [v for v in result.errors if v.reason_code is ReasonCode.CONTENT_SEVERITY_EXCEEDED]
    assert {v.details["category"] for v in violence} >= {"violence"}
    assert all(v.rule_id == "RATING_CONTENT_001" for v in violence)


def test_multiple_rating_violations_are_all_returned(engine):
    result = engine.evaluate_clip(clip("SC09", "00:09:19.000", "00:09:25.000", ["DLG_037"]), context("family"))

    rating = {
        (v.entity_id, v.details["category"])
        for v in result.errors
        if v.reason_code is ReasonCode.CONTENT_SEVERITY_EXCEEDED
    }
    assert rating == {("SC09", "violence"), ("SC09", "strong_language"), ("DLG_037", "strong_language")}


def test_same_content_is_allowed_for_a_permissive_audience(engine):
    result = engine.evaluate_clip(clip("SC09", "00:09:19.000", "00:09:25.000", ["DLG_037"]), context("young_adult"))

    assert ReasonCode.CONTENT_SEVERITY_EXCEEDED not in result.reason_codes


def test_flagged_scene_used_with_context_is_a_warning_only(engine):
    lines = ["DLG_031", "DLG_032", "DLG_033"]
    result = engine.evaluate_clip(clip("SC08", "00:07:54.000", "00:08:20.000", lines), context("young_adult"))

    assert result.eligible
    assert [(v.reason_code, v.entity_id) for v in result.warnings] == [(ReasonCode.MISLEADING_SCENE_CONTEXT, "SC08")]


def test_line_flagged_as_misleading_is_rejected(engine):
    result = engine.evaluate_clip(clip("SC08", "00:08:24.000", "00:08:31.000", ["DLG_034"]), context("young_adult"))

    assert error_codes(result) == {(ReasonCode.MISLEADING_DIALOGUE_IN_CLIP, "DLG_034")}
    assert ReasonCode.MISLEADING_SCENE_CONTEXT not in result.reason_codes


def test_trailer_longer_than_policy_is_rejected(engine):
    trailer = TrailerCandidate(
        trailer_id="TRL_TOO_LONG",
        clips=[
            clip("SC05", "00:04:20.000", "00:05:35.000", clip_id="CLIP_001"),
            clip("SC06", "00:05:35.000", "00:06:40.000", clip_id="CLIP_002"),
        ],
    )

    result = engine.evaluate_trailer(trailer, context("young_adult"))

    violation = next(v for v in result.errors if v.reason_code is ReasonCode.TRAILER_TOO_LONG)
    assert violation.details == {"duration_seconds": "140", "max_seconds": "90"}
