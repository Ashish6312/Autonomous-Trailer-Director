"""Replay planner: returns a recorded planner response from a local JSON file.

One file per audience (``<replay_dir>/<audience>.json``). Replays never touch
the network, so the same evidence plus the same replay file always yields the
same run.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from trailer_director.domain import AudienceType, ValidationSeverity
from trailer_director.domain.base import DomainModel
from trailer_director.domain.ids import EpisodeId, NonEmptyStr
from trailer_director.errors import PlannerError
from trailer_director.planning.models import IssueCode, PlannerMode, PlannerResponse, PlanningIssue, TokenUsage
from trailer_director.planning.pool import EvidencePool
from trailer_director.planning.strategy import AudienceStrategy
from trailer_director.story import StoryMap

REPLAY_FORMAT_VERSION = 1


class ReplayTarget(DomainModel):
    episode_id: EpisodeId
    audience: AudienceType
    evidence_fingerprint: NonEmptyStr


class ReplayRecord(DomainModel):
    format_version: Literal[1]
    planner_version: NonEmptyStr
    recorded_at: datetime
    recorded_for: ReplayTarget
    model_calls: int = Field(ge=0)
    usage: TokenUsage | None = None
    """Token counts of the recorded live call, if it was one; replayed so the run's accounting matches."""
    latency_seconds: float | None = None
    """Latency of the recorded live call. Kept for audit only: a replay makes no call and reports none."""
    response: Any


class ReplayPlanner:
    mode = PlannerMode.REPLAY
    version = f"replay-format-{REPLAY_FORMAT_VERSION}"

    def __init__(self, replay_dir: Path) -> None:
        self._replay_dir = replay_dir

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse:
        record = load_replay(self._replay_dir / f"{strategy.audience}.json")
        target = record.recorded_for
        issues = []
        if target.evidence_fingerprint != story_map.evidence_fingerprint or target.episode_id != story_map.episode_id:
            issues.append(
                PlanningIssue(
                    severity=ValidationSeverity.WARNING,
                    code=IssueCode.REPLAY_EVIDENCE_MISMATCH,
                    location="replay.recorded_for",
                    message=f"recorded against {target.episode_id}@{target.evidence_fingerprint}, "
                    f"replayed against {story_map.episode_id}@{story_map.evidence_fingerprint}",
                )
            )
        return PlannerResponse(
            mode=self.mode,
            planner_version=record.planner_version,
            payload=record.response,
            model_calls=record.model_calls,
            usage=record.usage,
            issues=issues,
        )


def load_replay(path: Path) -> ReplayRecord:
    try:
        return ReplayRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlannerError(f"replay file not found: {path}") from None
    except ValidationError as exc:
        raise PlannerError(f"{path}: invalid replay record: {exc.error_count()} error(s)\n{exc}") from None
