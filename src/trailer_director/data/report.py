from dataclasses import dataclass, field

from trailer_director.domain.enums import ValidationSeverity


@dataclass(frozen=True)
class ValidationIssue:
    severity: ValidationSeverity
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity.upper()} {self.location}: {self.message}"


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    def error(self, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(ValidationSeverity.ERROR, location, message))

    def warning(self, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(ValidationSeverity.WARNING, location, message))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def format(self) -> str:
        return "\n".join(str(issue) for issue in self.issues)
