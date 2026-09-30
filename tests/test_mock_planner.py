import pytest
from support import context

from trailer_director.domain import AudienceType, SpoilerLevel
from trailer_director.planning import (
    MockPlanner,
    build_audience_strategy,
    build_evidence_pool,
    normalize_planner_output,
)
from trailer_director.planning.heuristics import address_terms
from trailer_director.planning.pipeline import campaign_context

AUDIENCES = list(AudienceType)


@pytest.fixture(scope="module")
def plans(evidence, engine, story_map):
    """Mock plan per audience, for each audience's default campaign context."""
    planner = MockPlanner(evidence)
    result = {}
    for audience in AUDIENCES:
        ctx = campaign_context(evidence, audience)
        pool = build_evidence_pool(engine, evidence, ctx)
        response = planner.plan(story_map, build_audience_strategy(evidence, audience), pool)
        proposal, issues = normalize_planner_output(response.payload, evidence, audience)
        result[audience] = (response, proposal, issues, pool)
    return result


def _scene_ids(proposal):
    return {clip.scene_id for clip in proposal.candidate.clips}


def test_mock_plan_is_deterministic(evidence, engine, story_map):
    ctx = context("family")
    pool = build_evidence_pool(engine, evidence, ctx)
    strategy = build_audience_strategy(evidence, AudienceType.FAMILY)

    first = MockPlanner(evidence).plan(story_map, strategy, pool)
    second = MockPlanner(evidence).plan(story_map, strategy, pool)

    assert first == second


@pytest.mark.parametrize("audience", AUDIENCES)
def test_mock_output_normalises_without_issues(plans, audience):
    _, proposal, issues, _ = plans[audience]

    assert issues == []
    assert proposal is not None


def test_audiences_get_different_selections(plans):
    selections = [frozenset(_scene_ids(plans[audience][1])) for audience in AUDIENCES]

    assert len(set(selections)) == len(AUDIENCES)


def test_differences_follow_strategy_and_evidence(plans, evidence):
    family, young_adult, dialect = (plans[a][1] for a in AUDIENCES)
    terms = address_terms(evidence)

    assert any(evidence.scene(s).dramatic_function == "relationship" for s in _scene_ids(family))
    assert any(evidence.scene(s).dramatic_function == "conflict" for s in _scene_ids(young_adult))
    assert any("CHAR_BANSI" in evidence.scene(s).characters for s in _scene_ids(dialect))
    dialect_lines = [evidence.dialogue_line(d) for clip in dialect.candidate.clips for d in clip.dialogue_ids]
    assert any(term in line.text for line in dialect_lines for term in terms)


@pytest.mark.parametrize("audience", AUDIENCES)
def test_only_eligible_unprotected_evidence_is_selected(plans, story_map, evidence, audience):
    _, proposal, _, pool = plans[audience]
    protected_lines = {d for reveal in story_map.protected_reveals for d in reveal.dialogue_ids}

    for clip in proposal.candidate.clips:
        assert evidence.scene(clip.scene_id).spoiler_level is not SpoilerLevel.MAJOR
        assert all(pool.is_eligible(d) and d not in protected_lines for d in clip.dialogue_ids)
        assert clip.music_id is None or pool.is_eligible(clip.music_id)


@pytest.mark.parametrize("audience", AUDIENCES)
def test_every_clip_is_explained_with_its_own_evidence(plans, audience):
    _, proposal, _, _ = plans[audience]

    for clip, rationale in zip(proposal.candidate.clips, proposal.clip_rationales, strict=True):
        assert rationale.clip_id == clip.clip_id
        assert clip.scene_id in rationale.reason
        assert clip.dialogue_ids[0] in rationale.reason
        assert {clip.scene_id, *clip.dialogue_ids} <= set(rationale.evidence_ids)


@pytest.mark.parametrize("audience", AUDIENCES)
def test_plan_fits_the_audience_target_duration(plans, evidence, audience):
    _, proposal, _, _ = plans[audience]

    target_ms = evidence.audience_profile(audience).target_trailer_duration_seconds * 1000
    assert 0 < proposal.candidate.duration_ms <= target_ms


def test_music_choice_follows_rights_on_the_campaign_date(evidence, engine, story_map):
    strategy = build_audience_strategy(evidence, AudienceType.YOUNG_ADULT)

    def music_on(day):
        pool = build_evidence_pool(engine, evidence, context("young_adult", on=day))
        return {clip["music_id"] for clip in MockPlanner(evidence).plan(story_map, strategy, pool).payload["clips"]}

    assert music_on("2026-10-20") == {"MUS_03"}
    assert music_on("2026-11-14") != {"MUS_03"}
