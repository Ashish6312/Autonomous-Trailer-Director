"""Record live model responses as replay fixtures, so a live run can be replayed offline.

A recorded plan is a normal ``ReplayRecord`` and a recorded repair a normal
``ReplayRepairRecord``: ``ReplayPlanner`` / ``ReplayRepairPlanner`` read them
back unchanged, so the same evidence plus the same recording gives the same run.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from trailer_director.planning import AudienceStrategy, EvidencePool, Planner, PlannerResponse
from trailer_director.planning.replay import REPLAY_FORMAT_VERSION, ReplayRecord, ReplayTarget
from trailer_director.repair import RepairPlanner, RepairRequest
from trailer_director.repair.planners import RecordedRepair, ReplayRepairRecord
from trailer_director.story import StoryMap

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RecordingPlanner:
    def __init__(self, inner: Planner, out_dir: Path, clock: Clock = _utc_now) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._clock = clock
        self.mode = inner.mode
        self.version = inner.version

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse:
        response = self._inner.plan(story_map, strategy, pool)
        record = ReplayRecord(
            format_version=REPLAY_FORMAT_VERSION,
            planner_version=response.planner_version,
            recorded_at=self._clock(),
            recorded_for=_target(story_map, strategy),
            model_calls=response.model_calls,
            usage=response.usage,
            latency_seconds=response.latency_seconds,
            response=response.payload,
        )
        _write(self._out_dir / "planner_runs" / f"{strategy.audience}.json", record.model_dump_json(indent=2))
        return response


class RecordingRepairPlanner:
    def __init__(self, inner: RepairPlanner, out_dir: Path, clock: Clock = _utc_now) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._clock = clock
        self._attempts: list[RecordedRepair] = []
        self.mode = inner.mode
        self.version = inner.version

    def repair(self, request: RepairRequest) -> PlannerResponse:
        response = self._inner.repair(request)
        self._attempts.append(
            RecordedRepair(
                attempt=request.attempt,
                model_calls=response.model_calls,
                usage=response.usage,
                latency_seconds=response.latency_seconds,
                response=response.payload,
            )
        )
        record = ReplayRepairRecord(
            format_version=REPLAY_FORMAT_VERSION,
            planner_version=response.planner_version,
            recorded_at=self._clock(),
            recorded_for=_target(request.story_map, request.strategy),
            attempts=self._attempts,
        )
        _write(self._out_dir / "repair_runs" / f"{request.strategy.audience}.json", record.model_dump_json(indent=2))
        return response


def _target(story_map: StoryMap, strategy: AudienceStrategy) -> ReplayTarget:
    return ReplayTarget(
        episode_id=story_map.episode_id,
        audience=strategy.audience,
        evidence_fingerprint=story_map.evidence_fingerprint,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8", newline="\n")
