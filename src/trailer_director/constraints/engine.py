"""Deterministic eligibility decisions over a validated evidence package.

The engine runs every rule and returns every finding; it never stops at the
first failure, so a caller (later, a planner) can repair everything at once.
"""

from collections.abc import Sequence

from trailer_director.constraints.models import ConstraintContext, ConstraintViolation, EligibilityResult, EntityType
from trailer_director.constraints.rules import DEFAULT_CLIP_RULES, DEFAULT_TRAILER_RULES, ClipRule, TrailerRule
from trailer_director.constraints.rules.rights import music_rights_violations
from trailer_director.constraints.rules.source import dialogue_not_found, music_not_found, scene_not_found
from trailer_director.domain import EpisodePackage
from trailer_director.domain.edit import TrailerCandidate, TrailerClip
from trailer_director.domain.timecode import format_timecode


class ConstraintEngine:
    def __init__(
        self,
        evidence: EpisodePackage,
        clip_rules: Sequence[ClipRule] = DEFAULT_CLIP_RULES,
        trailer_rules: Sequence[TrailerRule] = DEFAULT_TRAILER_RULES,
    ) -> None:
        self._evidence = evidence
        self._clip_rules = tuple(clip_rules)
        self._trailer_rules = tuple(trailer_rules)

    def evaluate_clip(self, clip: TrailerClip, context: ConstraintContext) -> EligibilityResult:
        return self._result(EntityType.CLIP, clip.clip_id, context, self._clip_violations(clip, context))

    def evaluate_trailer(self, trailer: TrailerCandidate, context: ConstraintContext) -> EligibilityResult:
        violations = [v for clip in trailer.clips for v in self._clip_violations(clip, context)]
        for rule in self._trailer_rules:
            violations += rule.evaluate(trailer, context, self._evidence)
        return self._result(EntityType.TRAILER, trailer.trailer_id, context, violations)

    def evaluate_scene(self, scene_id: str, context: ConstraintContext) -> EligibilityResult:
        """Eligibility of a whole scene with all of its dialogue, and no trailer music."""
        scene = self._evidence.scenes.get(scene_id)
        if scene is None:
            return self._result(EntityType.SCENE, scene_id, context, [scene_not_found(scene_id)])
        clip = TrailerClip(
            clip_id=f"CLIP_{scene_id}",
            scene_id=scene_id,
            source_in=format_timecode(scene.source_in),
            source_out=format_timecode(scene.source_out),
            dialogue_ids=scene.dialogue_ids,
            purpose="whole-scene eligibility check",
        )
        return self._result(EntityType.SCENE, scene_id, context, self._unattached(clip, context))

    def evaluate_dialogue(self, dialogue_id: str, context: ConstraintContext) -> EligibilityResult:
        """Eligibility of one line cut tightly on its own timecodes."""
        line = self._evidence.dialogue.get(dialogue_id)
        if line is None:
            return self._result(EntityType.DIALOGUE, dialogue_id, context, [dialogue_not_found(dialogue_id)])
        clip = TrailerClip(
            clip_id=f"CLIP_{dialogue_id}",
            scene_id=line.scene_id,
            source_in=format_timecode(line.start),
            source_out=format_timecode(line.end),
            dialogue_ids=[dialogue_id],
            purpose="single-line eligibility check",
        )
        return self._result(EntityType.DIALOGUE, dialogue_id, context, self._unattached(clip, context))

    def evaluate_music(self, music_id: str, context: ConstraintContext) -> EligibilityResult:
        if music_id not in self._evidence.music:
            return self._result(EntityType.MUSIC, music_id, context, [music_not_found(music_id)])
        return self._result(
            EntityType.MUSIC, music_id, context, music_rights_violations(music_id, context, self._evidence)
        )

    def _clip_violations(self, clip: TrailerClip, context: ConstraintContext) -> list[ConstraintViolation]:
        return [violation for rule in self._clip_rules for violation in rule.evaluate(clip, context, self._evidence)]

    def _unattached(self, clip: TrailerClip, context: ConstraintContext) -> list[ConstraintViolation]:
        """Violations for an engine-built clip, without its internal clip ID."""
        return [v.model_copy(update={"clip_id": None}) for v in self._clip_violations(clip, context)]

    @staticmethod
    def _result(
        subject_type: EntityType, subject_id: str, context: ConstraintContext, violations: list[ConstraintViolation]
    ) -> EligibilityResult:
        return EligibilityResult(
            subject_type=subject_type, subject_id=subject_id, context=context, violations=violations
        )
