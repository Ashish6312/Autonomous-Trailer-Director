"""Repair planner interface, the replay repair planner, and a simulated provider outage.

A repair planner receives the rejected candidate and the repair decisions,
and proposes a revision. ``DeterministicRepairPlanner`` lives in
``repair.deterministic``; ``ReplayRepairPlanner`` returns recorded repair
responses; ``OutagePlanner`` makes a planner fail so retry and fallback can be
exercised offline.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError

from trailer_director.domain import ValidationSeverity
from trailer_director.domain.base import DomainModel
from trailer_director.domain.ids import NonEmptyStr
from trailer_director.errors import PlannerError
from trailer_director.planning import (
    EvidencePool,
    IssueCode,
    Planner,
    PlannerMode,
    PlannerResponse,
    PlanningIssue,
    TokenUsage,
)
from trailer_director.planning.replay import ReplayTarget
from trailer_director.planning.strategy import AudienceStrategy
from trailer_director.repair.models import RepairRequest
from trailer_director.story import StoryMap


class RepairPlanner(Protocol):
    """Revises a candidate. Its payload is untrusted and goes through the same normaliser as a plan."""

    mode: PlannerMode
    version: str

    def repair(self, request: RepairRequest) -> PlannerResponse: ...


class RecordedRepair(DomainModel):
    attempt: int = Field(ge=1)
    model_calls: int = Field(ge=0)
    usage: TokenUsage | None = None
    latency_seconds: float | None = None
    """Audit only: a replay makes no call and reports no latency."""
    response: Any


class ReplayRepairRecord(DomainModel):
    format_version: Literal[1]
    planner_version: NonEmptyStr
    recorded_at: datetime
    recorded_for: ReplayTarget
    attempts: list[RecordedRepair]


class ReplayRepairPlanner:
    """Recorded repair responses from ``<replay_dir>/<audience>.json``, one per repair attempt."""

    mode = PlannerMode.REPLAY
    version = "replay-repair-format-1"

    def __init__(self, replay_dir: Path) -> None:
        self._replay_dir = replay_dir

    def repair(self, request: RepairRequest) -> PlannerResponse:
        path = self._replay_dir / f"{request.strategy.audience}.json"
        record = load_repair_replay(path)
        recorded = next((a for a in record.attempts if a.attempt == request.attempt), None)
        if recorded is None:
            raise PlannerError(f"{path}: no recorded response for repair attempt {request.attempt}")
        issues = []
        if record.recorded_for.evidence_fingerprint != request.story_map.evidence_fingerprint:
            issues.append(
                PlanningIssue(
                    severity=ValidationSeverity.WARNING,
                    code=IssueCode.REPLAY_EVIDENCE_MISMATCH,
                    location="repair_replay.recorded_for",
                    message=f"recorded against evidence {record.recorded_for.evidence_fingerprint}",
                )
            )
        return PlannerResponse(
            mode=self.mode,
            planner_version=record.planner_version,
            payload=recorded.response,
            model_calls=recorded.model_calls,
            usage=recorded.usage,
            issues=issues,
        )


def load_repair_replay(path: Path) -> ReplayRepairRecord:
    try:
        return ReplayRepairRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlannerError(f"repair replay file not found: {path}") from None
    except ValidationError as exc:
        raise PlannerError(f"{path}: invalid repair replay record\n{exc}") from None


class OutagePlanner:
    """Wraps a planner and fails its first ``failures`` calls, as an unavailable provider would."""

    def __init__(self, inner: Planner, failures: int) -> None:
        self._inner = inner
        self._failures_left = failures
        self.mode = inner.mode
        self.version = inner.version

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise PlannerError("planner provider unavailable (simulated outage)")
        return self._inner.plan(story_map, strategy, pool)
