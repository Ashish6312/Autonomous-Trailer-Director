"""Historical performance as information only.

This rule only ever emits INFO. It exists so a planner sees the evidence
next to the verdict, and so tests can prove the evidence never changes it.
"""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.domain import EpisodePackage
from trailer_director.domain.edit import TrailerClip


class HistoricalPerformanceRule:
    rule_id = "PERFORMANCE_NOTE_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        records = [
            record
            for record in evidence.historical_performance
            if record.scene_id == clip.scene_id and record.audience is context.audience
        ]
        return [
            ConstraintViolation(
                rule_id=self.rule_id,
                severity=ConstraintSeverity.INFO,
                entity_type=EntityType.SCENE,
                entity_id=record.scene_id,
                reason_code=ReasonCode.HISTORICAL_PERFORMANCE,
                message=f"{record.campaign_id}: engagement {record.engagement_score:g}, "
                f"CTR {record.click_through_rate:.1%}, completion {record.completion_rate:.0%}. "
                "Evidence only; does not affect eligibility.",
                clip_id=clip.clip_id,
                details={"campaign_id": record.campaign_id, "engagement_score": f"{record.engagement_score:g}"},
            )
            for record in records
        ]
