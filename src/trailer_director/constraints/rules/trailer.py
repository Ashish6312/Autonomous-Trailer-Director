"""Rules that need the whole trailer rather than one clip."""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.domain import EpisodePackage
from trailer_director.domain.edit import TrailerCandidate


class TrailerDurationRule:
    rule_id = "POLICY_DURATION_001"

    def evaluate(
        self, trailer: TrailerCandidate, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        limit_seconds = evidence.rating_policy(context.audience).max_trailer_duration_seconds
        duration_seconds = trailer.duration_ms / 1000
        if duration_seconds <= limit_seconds:
            return []
        return [
            ConstraintViolation(
                rule_id=self.rule_id,
                severity=ConstraintSeverity.ERROR,
                entity_type=EntityType.TRAILER,
                entity_id=trailer.trailer_id,
                reason_code=ReasonCode.TRAILER_TOO_LONG,
                message=f"Trailer runs {duration_seconds:g}s; the {context.audience} policy allows {limit_seconds}s.",
                remediation="Shorten or remove clips.",
                details={"duration_seconds": f"{duration_seconds:g}", "max_seconds": str(limit_seconds)},
            )
        ]
