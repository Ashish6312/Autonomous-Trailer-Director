"""Source existence: a candidate may only reference evidence that exists."""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.domain import EpisodePackage
from trailer_director.domain.edit import TrailerClip

SCENE_RULE_ID = "SOURCE_SCENE_001"
DIALOGUE_RULE_ID = "SOURCE_DIALOGUE_001"
MUSIC_RULE_ID = "SOURCE_MUSIC_001"


def scene_not_found(scene_id: str, clip_id: str | None = None) -> ConstraintViolation:
    return ConstraintViolation(
        rule_id=SCENE_RULE_ID,
        severity=ConstraintSeverity.ERROR,
        entity_type=EntityType.SCENE,
        entity_id=scene_id,
        reason_code=ReasonCode.SCENE_NOT_FOUND,
        message=f"Scene {scene_id} does not exist in the episode evidence.",
        remediation="Use a scene ID from scenes.json.",
        clip_id=clip_id,
    )


def dialogue_not_found(dialogue_id: str, clip_id: str | None = None) -> ConstraintViolation:
    return ConstraintViolation(
        rule_id=DIALOGUE_RULE_ID,
        severity=ConstraintSeverity.ERROR,
        entity_type=EntityType.DIALOGUE,
        entity_id=dialogue_id,
        reason_code=ReasonCode.DIALOGUE_NOT_FOUND,
        message=f"Dialogue {dialogue_id} does not exist in the episode evidence.",
        remediation="Use a dialogue ID from dialogue.json.",
        clip_id=clip_id,
    )


def music_not_found(music_id: str, clip_id: str | None = None) -> ConstraintViolation:
    return ConstraintViolation(
        rule_id=MUSIC_RULE_ID,
        severity=ConstraintSeverity.ERROR,
        entity_type=EntityType.MUSIC,
        entity_id=music_id,
        reason_code=ReasonCode.MUSIC_NOT_FOUND,
        message=f"Music asset {music_id} does not exist in the rights data.",
        remediation="Use a music ID from music.json.",
        clip_id=clip_id,
    )


class SceneExistsRule:
    rule_id = SCENE_RULE_ID

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        if clip.scene_id in evidence.scenes:
            return []
        return [scene_not_found(clip.scene_id, clip.clip_id)]


class DialogueExistsRule:
    rule_id = DIALOGUE_RULE_ID

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        violations = []
        for dialogue_id in clip.dialogue_ids:
            line = evidence.dialogue.get(dialogue_id)
            if line is None:
                violations.append(dialogue_not_found(dialogue_id, clip.clip_id))
            elif line.scene_id != clip.scene_id:
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.ERROR,
                        entity_type=EntityType.DIALOGUE,
                        entity_id=dialogue_id,
                        reason_code=ReasonCode.DIALOGUE_SCENE_MISMATCH,
                        message=f"Dialogue {dialogue_id} belongs to scene {line.scene_id}, not {clip.scene_id}.",
                        remediation=f"Move the line to a clip from {line.scene_id} or remove it.",
                        clip_id=clip.clip_id,
                    )
                )
        return violations


class MusicExistsRule:
    rule_id = MUSIC_RULE_ID

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        if clip.music_id is None or clip.music_id in evidence.music:
            return []
        return [music_not_found(clip.music_id, clip.clip_id)]
