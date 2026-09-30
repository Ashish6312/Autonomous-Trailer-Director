from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from trailer_director.constraints import ConstraintContext, EligibilityResult, ReasonCode
from trailer_director.data import ValidationReport
from trailer_director.domain.edit import TrailerClip

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "candidates"
REPLAY_DIR = Path(__file__).resolve().parents[1] / "examples" / "planner_runs"
FIXED_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def record(records: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    return next(item for item in records if item[key] == value)


def assert_error(report: ValidationReport, location: str, fragment: str) -> None:
    matches = [issue for issue in report.errors if issue.location == location and fragment in issue.message]
    assert matches, f"expected ERROR at {location!r} containing {fragment!r}, got:\n{report.format() or '(no issues)'}"


def context(audience: str = "family", on: str = "2026-11-01", territory: str = "IN") -> ConstraintContext:
    return ConstraintContext(audience=audience, evaluation_date=date.fromisoformat(on), territory=territory)


def clip(
    scene_id: str,
    source_in: str,
    source_out: str,
    dialogue_ids: Sequence[str] = (),
    music_id: str | None = None,
    clip_id: str = "CLIP_001",
) -> TrailerClip:
    return TrailerClip(
        clip_id=clip_id,
        scene_id=scene_id,
        source_in=source_in,
        source_out=source_out,
        dialogue_ids=list(dialogue_ids),
        music_id=music_id,
        purpose="test",
    )


def error_codes(result: EligibilityResult) -> set[tuple[ReasonCode, str]]:
    """(reason code, entity ID) for every ERROR in the result."""
    return {(violation.reason_code, violation.entity_id) for violation in result.errors}
