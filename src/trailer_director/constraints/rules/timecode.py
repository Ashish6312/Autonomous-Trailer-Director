"""Timecode validity: clips stay inside their scene and declare the lines they contain.

``source_in < source_out`` and the timecode format are enforced by
``TrailerClip`` itself, so they are not re-checked here.
"""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.constraints.rules.base import lines_in_clip
from trailer_director.domain import EpisodePackage
from trailer_director.domain.edit import TrailerClip
from trailer_director.domain.timecode import format_timecode


class ClipWithinSceneRule:
    rule_id = "TIMECODE_CLIP_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        scene_range = {"scene_in": format_timecode(scene.source_in), "scene_out": format_timecode(scene.source_out)}
        violations = []
        if clip.source_in < scene.source_in:
            violations.append(
                self._violation(
                    clip,
                    ReasonCode.CLIP_STARTS_BEFORE_SCENE,
                    f"Clip starts at {format_timecode(clip.source_in)}, before scene {scene.scene_id} "
                    f"starts at {scene_range['scene_in']}.",
                    scene_range,
                )
            )
        if clip.source_out > scene.source_out:
            violations.append(
                self._violation(
                    clip,
                    ReasonCode.CLIP_ENDS_AFTER_SCENE,
                    f"Clip ends at {format_timecode(clip.source_out)}, after scene {scene.scene_id} "
                    f"ends at {scene_range['scene_out']}.",
                    scene_range,
                )
            )
        return violations

    def _violation(
        self, clip: TrailerClip, reason: ReasonCode, message: str, details: dict[str, str]
    ) -> ConstraintViolation:
        return ConstraintViolation(
            rule_id=self.rule_id,
            severity=ConstraintSeverity.ERROR,
            entity_type=EntityType.CLIP,
            entity_id=clip.clip_id,
            reason_code=reason,
            message=message,
            remediation=f"Trim the clip to {details['scene_in']}-{details['scene_out']}.",
            clip_id=clip.clip_id,
            details=details,
        )


class DialogueTimingRule:
    rule_id = "TIMECODE_DIALOGUE_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        violations = []
        for line in lines_in_clip(clip, evidence):
            line_range = f"{format_timecode(line.start)}-{format_timecode(line.end)}"
            declared = line.dialogue_id in clip.dialogue_ids
            fully_inside = clip.source_in <= line.start and line.end <= clip.source_out
            if declared and not fully_inside:
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.ERROR,
                        entity_type=EntityType.DIALOGUE,
                        entity_id=line.dialogue_id,
                        reason_code=ReasonCode.DIALOGUE_OUTSIDE_CLIP,
                        message=f"Dialogue {line.dialogue_id} ({line_range}) is not fully inside the clip.",
                        remediation="Extend the clip to cover the whole line or drop the line.",
                        clip_id=clip.clip_id,
                    )
                )
            elif not declared:
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.WARNING,
                        entity_type=EntityType.DIALOGUE,
                        entity_id=line.dialogue_id,
                        reason_code=ReasonCode.UNDECLARED_DIALOGUE_IN_CLIP,
                        message=f"Dialogue {line.dialogue_id} ({line_range}) is audible in the clip but not declared.",
                        remediation="Declare the line so it is subtitled and checked, or trim it out.",
                        clip_id=clip.clip_id,
                    )
                )
        return violations
