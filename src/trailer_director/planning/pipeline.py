"""One planning run: story map -> strategy -> pool -> planner -> normaliser -> constraint engine.

The planner proposes; the constraint engine decides eligibility. There is no
repair or retry here: a rejected or invalid proposal is recorded as such.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

from trailer_director.constraints import ConstraintContext, ConstraintEngine, EligibilityResult
from trailer_director.domain import AudienceType, EpisodePackage, ValidationSeverity
from trailer_director.errors import PlannerError
from trailer_director.planning.claims import check_hook
from trailer_director.planning.models import (
    IssueCode,
    PlannerResponse,
    PlanningIssue,
    PlanningRun,
    PlanProposal,
    RunStatus,
)
from trailer_director.planning.normalizer import normalize_planner_output
from trailer_director.planning.planner import Planner
from trailer_director.planning.pool import EvidencePool, build_evidence_pool
from trailer_director.planning.strategy import AudienceStrategy, build_audience_strategy
from trailer_director.story import StoryMap, build_story_map, unresolved_references

PLANNER_COST_ITEM = "planner_model_call"
_RUN_ID_LENGTH = 12


@dataclass(frozen=True)
class PlanningOutcome:
    run: PlanningRun
    story_map: StoryMap
    strategy: AudienceStrategy
    pool: EvidencePool


def campaign_context(
    evidence: EpisodePackage,
    audience: AudienceType,
    evaluation_date: date | None = None,
    territory: str | None = None,
) -> ConstraintContext:
    """Context with defaults: the release date, and the episode's region for the dialect-region audience
    (its country for everyone else)."""
    audience = AudienceType(audience)  # a plain "dialect_region" string must get the regional default too
    region = evidence.episode.region
    default_territory = region if audience is AudienceType.DIALECT_REGION else region.split("-", 1)[0]
    return ConstraintContext(
        audience=audience,
        evaluation_date=evaluation_date or evidence.episode.release_date,
        territory=territory or default_territory,
    )


def run_planning(
    evidence: EpisodePackage,
    engine: ConstraintEngine,
    planner: Planner,
    context: ConstraintContext,
    now: datetime | None = None,
) -> PlanningOutcome:
    story_map = build_story_map(evidence)
    strategy = build_audience_strategy(evidence, context.audience)
    pool = build_evidence_pool(engine, evidence, context)

    issues = [
        PlanningIssue(
            severity=ValidationSeverity.ERROR,
            code=IssueCode.STORY_MAP_UNRESOLVED_REFERENCE,
            location=location,
            message=f"'{entity_id}' does not exist in the evidence",
        )
        for location, entity_id in unresolved_references(story_map, evidence)
    ]
    response: PlannerResponse | None = None
    proposal: PlanProposal | None = None
    eligibility: EligibilityResult | None = None

    if issues:
        status = RunStatus.PLANNER_FAILED
    else:
        try:
            response = planner.plan(story_map, strategy, pool)
        except PlannerError as exc:
            issues.append(
                PlanningIssue(
                    severity=ValidationSeverity.ERROR,
                    code=IssueCode.PLANNER_FAILED,
                    location="planner",
                    message=str(exc),
                )
            )
            status = RunStatus.PLANNER_FAILED
        else:
            issues += response.issues
            proposal, normalization_issues = normalize_planner_output(response.payload, evidence, context.audience)
            issues += normalization_issues
            if proposal is not None:
                issues += check_hook(proposal, evidence, pool)
            if proposal is None or any(i.severity is ValidationSeverity.ERROR for i in issues):
                status = RunStatus.INVALID_OUTPUT
            else:
                eligibility = engine.evaluate_trailer(proposal.candidate, context)
                status = RunStatus.ELIGIBLE if eligibility.eligible else RunStatus.REJECTED

    planner_version = response.planner_version if response else planner.version
    model_calls = response.model_calls if response else 0
    run = PlanningRun(
        run_id=_run_id(story_map.evidence_fingerprint, context, planner.mode, planner_version, response),
        created_at=now or datetime.now(UTC),
        episode_id=evidence.episode.episode_id,
        evidence_version=evidence.episode.version,
        evidence_fingerprint=story_map.evidence_fingerprint,
        context=context,
        mode=planner.mode,
        planner_version=planner_version,
        status=status,
        pool=pool.summary(),
        proposal=proposal,
        eligibility=eligibility,
        issues=issues,
        estimated_duration_seconds=proposal.candidate.duration_ms / 1000 if proposal else None,
        model_calls=model_calls,
        estimated_model_cost=model_calls * _planner_call_cost(evidence),
        currency=evidence.cost_sheet.currency,
        usage=response.usage if response else None,
        latency_seconds=response.latency_seconds if response else None,
    )
    return PlanningOutcome(run=run, story_map=story_map, strategy=strategy, pool=pool)


def _planner_call_cost(evidence: EpisodePackage) -> float:
    return next(item.unit_cost for item in evidence.cost_sheet.unit_costs if item.item_id == PLANNER_COST_ITEM)


def _run_id(
    fingerprint: str, context: ConstraintContext, mode: str, planner_version: str, response: PlannerResponse | None
) -> str:
    """Content-derived ID: the same inputs and planner output always give the same run ID."""
    content = {
        "evidence": fingerprint,
        "context": context.model_dump(mode="json"),
        "mode": mode,
        "planner_version": planner_version,
        "payload": response.payload if response else None,
    }
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"RUN_{digest[:_RUN_ID_LENGTH]}"
