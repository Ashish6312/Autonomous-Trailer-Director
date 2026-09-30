"""Spoiler safety, using the spoiler levels recorded in the evidence."""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.constraints.rules.base import lines_in_clip
from trailer_director.domain import EpisodePackage, SpoilerLevel
from trailer_director.domain.edit import TrailerClip


class SpoilerRule:
    """Rejects footage or lines above the audience's spoiler ceiling.

    Scene-level spoiler flags apply to any clip from that scene, because the
    evidence has no shot-level spoiler metadata.
    """

    rule_id = "SPOILER_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        limit = evidence.rating_policy(context.audience).max_spoiler_level

        flagged = [(EntityType.SCENE, scene.scene_id, scene.spoiler_level)]
        flagged += [
            (EntityType.DIALOGUE, line.dialogue_id, line.spoiler_level) for line in lines_in_clip(clip, evidence)
        ]
        return [
            self._violation(entity_type, entity_id, level, limit, context, clip.clip_id)
            for entity_type, entity_id, level in flagged
            if level.rank > limit.rank
        ]

    def _violation(
        self,
        entity_type: EntityType,
        entity_id: str,
        level: SpoilerLevel,
        limit: SpoilerLevel,
        context: ConstraintContext,
        clip_id: str,
    ) -> ConstraintViolation:
        return ConstraintViolation(
            rule_id=self.rule_id,
            severity=ConstraintSeverity.ERROR,
            entity_type=entity_type,
            entity_id=entity_id,
            reason_code=ReasonCode.SPOILER_LEVEL_EXCEEDED,
            message=f"{entity_type.capitalize()} {entity_id} is a '{level}' spoiler; "
            f"the {context.audience} policy allows at most '{limit}'.",
            remediation=f"Use material with spoiler level '{limit}' or lower.",
            clip_id=clip_id,
            details={"spoiler_level": level, "max_spoiler_level": limit, "audience": context.audience},
        )
