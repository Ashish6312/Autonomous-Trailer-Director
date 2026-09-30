"""Rating policy: content flags against the audience's per-category limits."""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.constraints.rules.base import lines_in_clip
from trailer_director.domain import EpisodePackage, RatingPolicy, SensitiveCategory, SensitiveContent
from trailer_director.domain.edit import TrailerClip


class ContentRatingRule:
    """Checks every content category except misleading context, which has its own rule.

    Scene-level flags apply to any clip from that scene, because the
    evidence has no shot-level content metadata.
    """

    rule_id = "RATING_CONTENT_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        policy = evidence.rating_policy(context.audience)

        sources = [(EntityType.SCENE, scene.scene_id, scene.sensitive_content)]
        sources += [
            (EntityType.DIALOGUE, line.dialogue_id, line.sensitive_content) for line in lines_in_clip(clip, evidence)
        ]
        return [
            self._violation(entity_type, entity_id, flag, policy, clip.clip_id)
            for entity_type, entity_id, flags in sources
            for flag in flags
            if flag.category is not SensitiveCategory.MISLEADING_CONTEXT
            and flag.severity.rank > policy.max_severity_for(flag.category).rank
        ]

    def _violation(
        self, entity_type: EntityType, entity_id: str, flag: SensitiveContent, policy: RatingPolicy, clip_id: str
    ) -> ConstraintViolation:
        allowed = policy.max_severity_for(flag.category)
        return ConstraintViolation(
            rule_id=self.rule_id,
            severity=ConstraintSeverity.ERROR,
            entity_type=entity_type,
            entity_id=entity_id,
            reason_code=ReasonCode.CONTENT_SEVERITY_EXCEEDED,
            message=f"{entity_type.capitalize()} {entity_id} has '{flag.severity}' {flag.category}; "
            f"the {policy.audience} policy allows '{allowed}'.",
            remediation=f"Use material with {flag.category} at most '{allowed}'.",
            clip_id=clip_id,
            details={
                "category": flag.category,
                "severity": flag.severity,
                "max_severity": allowed,
                "audience": policy.audience,
            },
        )
