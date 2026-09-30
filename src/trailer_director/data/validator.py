"""Deterministic dataset validation.

Runs in two phases: per-record schema parsing, then cross-record checks on
whatever parsed successfully. All problems are collected into one report so
a data author sees every issue in a single run.
"""

from collections.abc import Mapping
from typing import Any

from trailer_director.data.commercial_checks import check_commercial
from trailer_director.data.parsing import parse_dataset
from trailer_director.data.report import ValidationReport
from trailer_director.data.story_checks import check_story
from trailer_director.domain import EpisodePackage


def validate_dataset(raw: Mapping[str, Any]) -> tuple[EpisodePackage | None, ValidationReport]:
    """Validate raw dataset JSON. The package is ``None`` whenever the report has errors."""
    report = ValidationReport()
    parsed = parse_dataset(raw, report)
    check_story(parsed, report)
    check_commercial(parsed, report)
    if not report.is_valid:
        return None, report
    return parsed.to_package(), report
