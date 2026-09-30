"""Accessibility (subtitle safety), dialect review and prop continuity rules."""

from support import clip, context

from trailer_director.constraints import ConstraintSeverity, ReasonCode
from trailer_director.domain import AudienceType
from trailer_director.domain.edit import TrailerCandidate
from trailer_director.planning import MockPlanner, run_planning
from trailer_director.planning.pipeline import campaign_context


def _warnings(result, code):
    return [(w.entity_id, w.details) for w in result.warnings if w.reason_code is code]


def test_line_not_subtitle_safe_needs_review_but_stays_eligible(engine):
    result = engine.evaluate_clip(clip("SC06", "00:05:44.500", "00:05:50.500", ["DLG_025"]), context("family"))

    assert result.eligible
    [warning] = [w for w in result.warnings if w.reason_code is ReasonCode.SUBTITLE_REVIEW_REQUIRED]
    assert (warning.entity_id, warning.severity) == ("DLG_025", ConstraintSeverity.WARNING)
    assert "Chacha" in warning.message


def test_subtitle_safe_line_needs_no_review(engine):
    result = engine.evaluate_clip(clip("SC06", "00:05:50.500", "00:05:55.500", ["DLG_026"]), context("family"))

    assert ReasonCode.SUBTITLE_REVIEW_REQUIRED not in result.reason_codes


def test_regional_terms_need_dialect_review_only_where_policy_requires_it(engine):
    candidate = clip("SC01", "00:00:29.500", "00:00:33.500", ["DLG_004"])

    dialect = engine.evaluate_clip(candidate, context("dialect_region", territory="IN-HR"))
    family = engine.evaluate_clip(clip("SC03", "00:02:22.500", "00:02:27.500", ["DLG_012"]), context("family"))

    assert _warnings(dialect, ReasonCode.DIALECT_REVIEW_REQUIRED) == [
        ("DLG_004", {"terms": "Kaka", "addresses": "CHAR_BANSI"})
    ]
    assert dialect.eligible
    assert ReasonCode.DIALECT_REVIEW_REQUIRED not in family.reason_codes


def test_reversed_cut_order_flags_prop_continuity(engine):
    reveal = clip("SC10", "00:10:24.500", "00:10:30.500", ["DLG_039"], clip_id="CLIP_001")
    homecoming = clip("SC02", "00:01:04.500", "00:01:09.000", ["DLG_005"], clip_id="CLIP_002")

    reversed_order = engine.evaluate_trailer(
        TrailerCandidate(trailer_id="TRL_X", clips=[reveal, homecoming]), context()
    )
    story_order = engine.evaluate_trailer(TrailerCandidate(trailer_id="TRL_X", clips=[homecoming, reveal]), context())

    flagged = {w.entity_id for w in reversed_order.warnings if w.reason_code is ReasonCode.CONTINUITY_ORDER_REVERSED}
    assert flagged == {"PROP_LETTER_01", "PROP_PHOTO_01"}
    assert ReasonCode.CONTINUITY_ORDER_REVERSED not in story_order.reason_codes


def test_line_level_review_warnings_do_not_demote_scenes_in_planning(evidence, engine):
    ctx = campaign_context(evidence, AudienceType.DIALECT_REGION)

    run = run_planning(evidence, engine, MockPlanner(evidence), ctx).run

    assert run.eligibility.eligible
    assert "SC01" in {c.scene_id for c in run.proposal.candidate.clips}
    assert ReasonCode.DIALECT_REVIEW_REQUIRED in run.eligibility.reason_codes


def test_possessive_form_of_a_regional_term_needs_dialect_review(engine):
    # DLG_025: "... you flattened Chacha's mustard?"
    tractor = clip("SC06", "00:05:44.500", "00:05:50.500", ["DLG_025"])

    result = engine.evaluate_clip(tractor, context("dialect_region", territory="IN-HR"))

    [(entity_id, details)] = _warnings(result, ReasonCode.DIALECT_REVIEW_REQUIRED)
    assert entity_id == "DLG_025"
    assert details["terms"] == "Chacha, Papa"
