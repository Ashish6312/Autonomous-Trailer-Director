from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trailer_director.data.report import ValidationReport


class TrailerDirectorError(Exception):
    """Base class for project errors."""


class DatasetLoadError(TrailerDirectorError):
    """A dataset file is missing or is not valid JSON."""


class DatasetValidationError(TrailerDirectorError):
    """The dataset loaded but violates schema or referential rules."""

    def __init__(self, report: "ValidationReport") -> None:
        self.report = report
        super().__init__(f"dataset failed validation with {len(report.errors)} error(s)")


class PlannerError(TrailerDirectorError):
    """A planner could not produce any output (e.g. a replay file is missing, or the model API failed).

    ``retryable`` is False when trying again cannot help (bad credentials, a 4xx, a refusal).
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class UnknownEntityError(TrailerDirectorError, KeyError):
    """A lookup referenced an ID that does not exist in the source evidence."""

    def __init__(self, kind: str, entity_id: str) -> None:
        self.kind = kind
        self.entity_id = entity_id
        super().__init__(f"unknown {kind} id '{entity_id}'")

    def __str__(self) -> str:
        return self.args[0]
