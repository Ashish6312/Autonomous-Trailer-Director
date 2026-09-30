"""Verify -> decide -> repair -> re-verify, with retry, fallback and a budget.

1. Initial plan, or for ``replan`` re-verification of an existing plan under
   new conditions. Provider failures are retried with backoff
   (``max_provider_retries``), then the fallback planner is used if the cost
   sheet allows. An unavailable repairer is replaced by the fallback repairer
   for the rest of the run.
2. While not eligible: diagnose failing clips, turn violations into repair
   decisions (``repair.strategy``), have the repairer carry them out, check it
   changed only what the decisions allow, and re-run the engine.
3. Stop on eligibility, attempts or budget exhausted, or no progress.

The constraint engine alone decides eligibility.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from trailer_director.constraints import ConstraintContext, ConstraintEngine, EntityType
from trailer_director.domain import EpisodePackage, ValidationSeverity
from trailer_director.errors import PlannerError
from trailer_director.planning import (
    AudienceStrategy,
    EvidencePool,
    IssueCode,
    Planner,
    PlannerMode,
    PlannerResponse,
    PlanningIssue,
    PlanProposal,
    RunStatus,
    build_audience_strategy,
    build_evidence_pool,
    normalize_planner_output,
)
from trailer_director.planning.claims import check_hook
from trailer_director.repair.audit import build_audit_trail
from trailer_director.repair.budget import PLANNER_CALL_ITEM, REPAIR_CALL_ITEM, BudgetLedger
from trailer_director.repair.models import (
    Attempt,
    AttemptKind,
    ClipAction,
    LoopStatus,
    RepairDecision,
    RepairRequest,
    RepairRun,
    RepairScope,
    Verdict,
    VerificationCode,
)
from trailer_director.repair.planners import RepairPlanner
from trailer_director.repair.strategy import decide_repairs
from trailer_director.repair.verification import check_rationales, check_repair, clip_changes, diagnose
from trailer_director.story import StoryMap, build_story_map

DEFAULT_MAX_REPAIR_ATTEMPTS = 3
_RUN_ID_LENGTH = 12


class _BudgetExhausted(Exception):
    pass


@dataclass
class _RunState:
    context: ConstraintContext
    story_map: StoryMap
    strategy: AudienceStrategy
    pool: EvidencePool
    ledger: BudgetLedger
    attempts: list[Attempt] = field(default_factory=list)
    excluded_ids: set[str] = field(default_factory=set)
    repairer_down: bool = False
    fallbacks: list[str] = field(default_factory=list)


class RepairLoop:
    def __init__(
        self,
        evidence: EpisodePackage,
        engine: ConstraintEngine,
        repairer: RepairPlanner,
        *,
        fallback_planner: Planner | None = None,
        fallback_repairer: RepairPlanner | None = None,
        max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
        max_model_calls: int | None = None,
        max_cost: float | None = None,
        allow_fallback: bool = True,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._evidence = evidence
        self._engine = engine
        self._repairer = repairer
        self._fallback = fallback_planner
        self._fallback_repairer = fallback_repairer
        self._max_repair_attempts = max_repair_attempts
        self._max_model_calls = max_model_calls
        self._max_cost = max_cost
        self._allow_fallback = allow_fallback
        self._sleep = sleep or time.sleep

    def run(self, planner: Planner, context: ConstraintContext, now: datetime | None = None) -> RepairRun:
        state = self._start(context)
        try:
            first = self._initial_attempt(state, planner)
        except _BudgetExhausted:
            return self._finish(state, LoopStatus.BUDGET_EXHAUSTED, now)
        if first.status is RunStatus.PLANNER_FAILED:
            return self._finish(state, LoopStatus.PLANNER_UNAVAILABLE, now)
        return self._repair_until_done(state, first, now)

    def replan(self, previous: PlanProposal, context: ConstraintContext, now: datetime | None = None) -> RepairRun:
        """Conditions changed (date, territory, rights data): re-verify a plan and repair only what broke."""
        if previous.audience != context.audience:
            raise ValueError(f"plan is for {previous.audience}, context is for {context.audience}")
        state = self._start(context)
        first = self._evaluated_attempt(state, AttemptKind.REVERIFY, "none", "reverify", previous, [], None, [])
        state.attempts.append(first)
        return self._repair_until_done(state, first, now)

    def _start(self, context: ConstraintContext) -> _RunState:
        return _RunState(
            context=context,
            story_map=build_story_map(self._evidence),
            strategy=build_audience_strategy(self._evidence, context.audience),
            pool=build_evidence_pool(self._engine, self._evidence, context),
            ledger=BudgetLedger(
                self._evidence.cost_sheet, self._max_repair_attempts, self._max_model_calls, self._max_cost
            ),
        )

    def _initial_attempt(self, state: _RunState, planner: Planner) -> Attempt:
        response = self._call(
            state,
            AttemptKind.INITIAL,
            planner.mode,
            planner.version,
            PLANNER_CALL_ITEM,
            lambda: planner.plan(state.story_map, state.strategy, state.pool),
        )
        if response is None and self._fallback is not None and self._fallback_allowed():
            fallback = self._fallback
            state.fallbacks.append(f"planner {planner.mode} unavailable -> {fallback.mode} {fallback.version}")
            response = self._call(
                state,
                AttemptKind.FALLBACK,
                fallback.mode,
                fallback.version,
                PLANNER_CALL_ITEM,
                lambda: fallback.plan(state.story_map, state.strategy, state.pool),
                retries=0,
            )
            kind = AttemptKind.FALLBACK
        else:
            kind = AttemptKind.RETRY if state.attempts else AttemptKind.INITIAL
        if response is None:
            return state.attempts[-1]
        attempt = self._from_response(state, kind, response, PLANNER_CALL_ITEM, None, [])
        state.attempts.append(attempt)
        return attempt

    def _repair_until_done(self, state: _RunState, last: Attempt, now: datetime | None) -> RepairRun:
        current = last if last.status in (RunStatus.ELIGIBLE, RunStatus.REJECTED) else None
        while last.status is not RunStatus.ELIGIBLE:
            if state.ledger.repair_attempts >= self._max_repair_attempts:
                return self._finish(state, LoopStatus.ATTEMPTS_EXHAUSTED, now)
            request = self._repair_request(state, current, last)
            state.ledger.repair_attempts += 1
            try:
                response = self._request_repair(state, request)
            except _BudgetExhausted:
                return self._finish(state, LoopStatus.BUDGET_EXHAUSTED, now)
            if response is None:
                return self._finish(state, LoopStatus.REPAIR_FAILED, now)

            previous = current.proposal if current else None
            last = self._from_response(
                state, AttemptKind.REPAIR, response, REPAIR_CALL_ITEM, previous, request.decisions
            )
            state.attempts.append(last)
            if last.status in (RunStatus.ELIGIBLE, RunStatus.REJECTED):
                current = last
            elif any(i.code is VerificationCode.NO_PROGRESS for i in last.verification_issues):
                return self._finish(state, LoopStatus.REPAIR_FAILED, now)
        return self._finish(state, LoopStatus.ACCEPTED, now)

    def _request_repair(self, state: _RunState, request: RepairRequest) -> PlannerResponse | None:
        """Ask the repair planner; if it is unavailable, switch to the fallback repairer for the rest of the run."""
        primary, fallback = self._repairer, self._fallback_repairer
        if not state.repairer_down:
            response = self._call(
                state,
                AttemptKind.REPAIR,
                primary.mode,
                primary.version,
                REPAIR_CALL_ITEM,
                lambda: primary.repair(request),
            )
            if response is not None or fallback is None or not self._fallback_allowed():
                return response
            state.repairer_down = True
            state.fallbacks.append(f"repairer {primary.mode} unavailable -> {fallback.mode} {fallback.version}")
        if fallback is None:
            return None
        return self._call(
            state,
            AttemptKind.REPAIR,
            fallback.mode,
            fallback.version,
            REPAIR_CALL_ITEM,
            lambda: fallback.repair(request),
            retries=0,
        )

    def _fallback_allowed(self) -> bool:
        strategy = self._evidence.cost_sheet.fallback_strategy
        return self._allow_fallback and strategy.on_provider_unavailable == "use_replay_fixtures"

    def _repair_request(self, state: _RunState, current: Attempt | None, last: Attempt) -> RepairRequest:
        failing, trailer_errors = (
            diagnose(current.proposal, current.eligibility) if current and current.eligibility else ([], [])
        )
        decisions = decide_repairs(current.proposal, failing, trailer_errors) if current else []
        state.excluded_ids |= _excluded_by(decisions, {d.clip_id: d.scene_id for d in failing})
        return RepairRequest(
            attempt=state.ledger.repair_attempts + 1,
            story_map=state.story_map,
            strategy=state.strategy,
            pool=state.pool,
            proposal=current.proposal if current else None,
            failing_clips=failing,
            trailer_errors=trailer_errors,
            decisions=decisions,
            planning_issues=[] if last is current else last.planning_issues,
            excluded_ids=sorted(state.excluded_ids),
        )

    def _call(
        self,
        state: _RunState,
        kind: AttemptKind,
        mode: PlannerMode,
        version: str,
        cost_item: str,
        call: Callable[[], PlannerResponse],
        retries: int | None = None,
    ) -> PlannerResponse | None:
        """Call a planner with retry and exponential backoff; failed calls are recorded as attempts."""
        fallback = self._evidence.cost_sheet.fallback_strategy
        retries = fallback.max_provider_retries if retries is None else retries
        expected_calls = 0 if mode is PlannerMode.MOCK else 1
        for try_number in range(retries + 1):
            if try_number:
                self._sleep(fallback.retry_backoff_seconds * 2 ** (try_number - 1))
            if not state.ledger.can_afford(cost_item, expected_calls):
                raise _BudgetExhausted
            try:
                return call()
            except PlannerError as exc:
                state.attempts.append(
                    Attempt(
                        number=len(state.attempts),
                        kind=AttemptKind.RETRY if try_number else kind,
                        planner_mode=mode,
                        planner_version=version,
                        status=RunStatus.PLANNER_FAILED,
                        planning_issues=[
                            PlanningIssue(
                                severity=ValidationSeverity.ERROR,
                                code=IssueCode.PLANNER_FAILED,
                                location="planner",
                                message=str(exc),
                            )
                        ],
                    )
                )
                if not exc.retryable:
                    break
        return None

    def _from_response(
        self,
        state: _RunState,
        kind: AttemptKind,
        response: PlannerResponse,
        cost_item: str,
        previous: PlanProposal | None,
        decisions: list[RepairDecision],
    ) -> Attempt:
        cost = state.ledger.charge(cost_item, response.model_calls, response.usage)
        proposal, issues = normalize_planner_output(response.payload, self._evidence, state.context.audience)
        if proposal is not None:
            issues += check_hook(proposal, self._evidence, state.pool)
            if any(issue.severity is ValidationSeverity.ERROR for issue in issues):
                # An invented hook discards the plan, as unknown IDs do.
                proposal = None
        attempt_args = (response.mode, response.planner_version)
        if proposal is None:
            return Attempt(
                number=len(state.attempts),
                kind=kind,
                planner_mode=attempt_args[0],
                planner_version=attempt_args[1],
                status=RunStatus.INVALID_OUTPUT,
                decisions=decisions,
                planning_issues=[*response.issues, *issues],
                model_calls=response.model_calls,
                estimated_cost=cost,
                usage=response.usage,
                latency_seconds=response.latency_seconds,
            )
        attempt = self._evaluated_attempt(
            state, kind, *attempt_args, proposal, [*response.issues, *issues], previous, decisions
        )
        return attempt.model_copy(
            update={
                "model_calls": response.model_calls,
                "estimated_cost": cost,
                "usage": response.usage,
                "latency_seconds": response.latency_seconds,
            }
        )

    def _evaluated_attempt(
        self,
        state: _RunState,
        kind: AttemptKind,
        mode: str,
        version: str,
        proposal: PlanProposal,
        planning_issues: list[PlanningIssue],
        previous: PlanProposal | None,
        decisions: list[RepairDecision],
    ) -> Attempt:
        changes = clip_changes(previous, proposal) if previous else []
        verification = [*(check_repair(previous, proposal, decisions, changes) if previous else [])]
        verification += check_rationales(proposal)
        common = {
            "number": len(state.attempts),
            "kind": kind,
            "planner_mode": mode,
            "planner_version": version,
            "proposal": proposal,
            "decisions": decisions,
            "changes": changes,
            "planning_issues": planning_issues,
            "verification_issues": verification,
        }
        if any(issue.severity is ValidationSeverity.ERROR for issue in verification):
            return Attempt(status=RunStatus.INVALID_OUTPUT, **common)
        eligibility = self._engine.evaluate_trailer(proposal.candidate, state.context)
        failing, _ = diagnose(proposal, eligibility)
        status = RunStatus.ELIGIBLE if eligibility.eligible else RunStatus.REJECTED
        return Attempt(status=status, eligibility=eligibility, failing_clips=failing, **common)

    def _finish(self, state: _RunState, status: LoopStatus, now: datetime | None) -> RepairRun:
        evaluated = [a for a in state.attempts if a.eligibility is not None]
        final = evaluated[-1] if evaluated else None
        accepted = status is LoopStatus.ACCEPTED
        summary = _summary(status, state.attempts)
        return RepairRun(
            run_id=_run_id(state),
            created_at=now or datetime.now(UTC),
            episode_id=self._evidence.episode.episode_id,
            evidence_fingerprint=state.story_map.evidence_fingerprint,
            context=state.context,
            status=status,
            verdict=_verdict(accepted, final),
            pool=state.pool.summary(),
            fallbacks=state.fallbacks,
            final_proposal=final.proposal if final and accepted else None,
            final_eligibility=final.eligibility if final else None,
            attempts=state.attempts,
            audit_trail=build_audit_trail(state.attempts, summary, state.fallbacks),
            budget=state.ledger.usage(),
            summary=summary,
        )


def _verdict(accepted: bool, final: Attempt | None) -> Verdict:
    if not accepted or final is None or final.eligibility is None:
        return Verdict.REJECTED
    return Verdict.PASS_WITH_WARNINGS if final.eligibility.warnings else Verdict.PASS


def _summary(status: LoopStatus, attempts: list[Attempt]) -> str:
    repairs = [a for a in attempts if a.kind is AttemptKind.REPAIR and a.status is not RunStatus.PLANNER_FAILED]
    text = f"{status} after {len(repairs)} repair attempt(s)"
    changed = [a for a in repairs if a.changes]
    if changed:
        by_action: dict[ClipAction, list[str]] = {}
        for change in changed[-1].changes:
            label = change.clip_id
            if change.action is ClipAction.REPLACED:
                label += f" ({change.previous_scene_id} -> {change.scene_id})"
            by_action.setdefault(change.action, []).append(label)
        text += "; last repair: " + "; ".join(f"{action} {', '.join(ids)}" for action, ids in by_action.items())
    return text


def _excluded_by(decisions: list[RepairDecision], scene_of: dict[str, str]) -> set[str]:
    """Evidence a repair must not reuse: the offending track, line, or (for footage problems) scene."""
    excluded = set()
    for decision in decisions:
        if decision.scope is RepairScope.CLIP:
            excluded.add(scene_of[decision.clip_id])
        elif decision.scope is RepairScope.MUSIC:
            excluded |= {ref.entity_id for ref in decision.triggered_by}
        elif decision.scope is RepairScope.DIALOGUE:
            excluded |= {ref.entity_id for ref in decision.triggered_by if ref.entity_type is EntityType.DIALOGUE}
    return excluded


def _run_id(state: _RunState) -> str:
    content = state.story_map.evidence_fingerprint + state.context.model_dump_json()
    content += "".join(attempt.model_dump_json() for attempt in state.attempts)
    return f"REPAIR_{hashlib.sha256(content.encode('utf-8')).hexdigest()[:_RUN_ID_LENGTH]}"
