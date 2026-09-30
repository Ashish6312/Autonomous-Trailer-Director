"""Misleading context, decided from metadata only (no semantic interpretation yet)."""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.constraints.rules.base import lines_in_clip
from trailer_director.domain import ContentSeverity, EpisodePackage, SensitiveCategory, SensitiveContent
from trailer_director.domain.edit import TrailerClip


def _misleading_severity(flags: list[SensitiveContent]) -> ContentSeverity:
    levels = [flag.severity for flag in flags if flag.category is SensitiveCategory.MISLEADING_CONTEXT]
    return max(levels, key=lambda level: level.rank, default=ContentSeverity.NONE)


class MisleadingContextRule:
    """Using a line that is itself flagged as misleading out of context is an ERROR.

    A clip from a scene flagged as misleading, without such a line, is a
    WARNING: the scene can be cut honestly, but a person must confirm that
    the edit keeps the context.
    """

    rule_id = "CONTEXT_MISLEADING_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        allowed = evidence.rating_policy(context.audience).max_severity_for(SensitiveCategory.MISLEADING_CONTEXT)

        flagged_lines = [
            line
            for line in lines_in_clip(clip, evidence)
            if _misleading_severity(line.sensitive_content).rank > allowed.rank
        ]
        if flagged_lines:
            return [
                ConstraintViolation(
                    rule_id=self.rule_id,
                    severity=ConstraintSeverity.ERROR,
                    entity_type=EntityType.DIALOGUE,
                    entity_id=line.dialogue_id,
                    reason_code=ReasonCode.MISLEADING_DIALOGUE_IN_CLIP,
                    message=f"Dialogue {line.dialogue_id} is flagged as misleading out of context.",
                    remediation="Drop the line or include the surrounding lines that establish its meaning.",
                    clip_id=clip.clip_id,
                    details={"severity": _misleading_severity(line.sensitive_content), "max_severity": allowed},
                )
                for line in flagged_lines
            ]

        scene_level = _misleading_severity(scene.sensitive_content)
        if scene_level.rank <= allowed.rank:
            return []
        return [
            ConstraintViolation(
                rule_id=self.rule_id,
                severity=ConstraintSeverity.WARNING,
                entity_type=EntityType.SCENE,
                entity_id=scene.scene_id,
                reason_code=ReasonCode.MISLEADING_SCENE_CONTEXT,
                message=f"Scene {scene.scene_id} is flagged as easy to misread out of context.",
                remediation="Have an editor confirm the cut keeps the context that explains the action.",
                clip_id=clip.clip_id,
                details={"severity": scene_level, "max_severity": allowed},
            )
        ]
