"""Audience strategy and eligible evidence pool."""

from dataclasses import replace

import pytest
from support import context

from trailer_director.constraints import ConstraintSeverity, ReasonCode
from trailer_director.domain import AudienceType
from trailer_director.planning import build_audience_strategy, build_evidence_pool


@pytest.mark.parametrize("audience", list(AudienceType))
def test_strategy_is_derived_from_profile_and_policy(evidence, audience):
    profile = evidence.audience_profile(audience)
    policy = evidence.rating_policy(audience)

    strategy = build_audience_strategy(evidence, audience)

    assert strategy.positioning == profile.positioning_notes
    assert strategy.preferred_themes == profile.preferred_themes
    assert strategy.preferred_tones == profile.preferred_tones
    assert strategy.avoid == profile.avoid
    assert strategy.evidence == profile.evidence
    assert strategy.target_duration_seconds == profile.target_trailer_duration_seconds
    assert strategy.max_spoiler_level is policy.max_spoiler_level
    assert strategy.content_limits == {rule.category: rule.max_severity for rule in policy.content_rules}
    assert strategy.dialect_review_required is policy.dialect_review_required


def test_strategy_follows_the_loaded_profile_not_a_copy(evidence):
    profile = evidence.audience_profile(AudienceType.FAMILY).model_copy(update={"positioning_notes": "Changed."})
    changed = replace(evidence, audience_profiles={**evidence.audience_profiles, AudienceType.FAMILY: profile})

    assert build_audience_strategy(changed, AudienceType.FAMILY).positioning == "Changed."


def test_audiences_get_different_hook_types(evidence):
    hook_types = [tuple(build_audience_strategy(evidence, a).hook_types) for a in AudienceType]

    assert len(set(hook_types)) == len(hook_types)


def test_pool_keeps_eligible_and_excludes_ineligible_scenes(engine, evidence):
    pool = build_evidence_pool(engine, evidence, context("family", on="2026-11-14"))

    assert pool.eligible_scene_ids == ["SC02", "SC03", "SC05", "SC06"]
    assert not pool.is_eligible("SC10")
    assert ReasonCode.SPOILER_LEVEL_EXCEEDED in {v.reason_code for v in pool.rejection_for("SC10")}


def test_every_item_is_either_eligible_or_rejected_with_reasons(engine, evidence):
    pool = build_evidence_pool(engine, evidence, context("young_adult"))

    eligible = len(pool.eligible_scene_ids) + len(pool.eligible_dialogue_ids) + len(pool.eligible_music_ids)
    assert eligible + len(pool.rejected) == len(evidence.scenes) + len(evidence.dialogue) + len(evidence.music)
    for entry in pool.rejected:
        assert any(v.severity is ConstraintSeverity.ERROR for v in entry.violations), entry.entity_id
    for entry in [*pool.rejected, *pool.flagged]:
        assert all(v.severity is not ConstraintSeverity.INFO for v in entry.violations)


def test_pool_reflects_the_campaign_date(engine, evidence):
    before = build_evidence_pool(engine, evidence, context("young_adult", on="2026-10-20"))
    after = build_evidence_pool(engine, evidence, context("young_adult", on="2026-11-14"))

    assert before.is_eligible("MUS_03")
    assert not after.is_eligible("MUS_03")
    assert {v.reason_code for v in after.rejection_for("MUS_03")} == {ReasonCode.PROMOTIONAL_RIGHTS_EXPIRED}


def test_pool_is_deterministic(engine, evidence):
    ctx = context("dialect_region", territory="IN-HR")

    assert build_evidence_pool(engine, evidence, ctx) == build_evidence_pool(engine, evidence, ctx)
