"""Prop continuity across the trailer's cut order."""

from trailer_director.constraints.models import (
    ConstraintContext,
    ConstraintSeverity,
    ConstraintViolation,
    EntityType,
    ReasonCode,
)
from trailer_director.domain import EpisodePackage, PropState, Scene
from trailer_director.domain.edit import TrailerCandidate


class PropContinuityRule:
    """A tracked prop shown in a later-cut clip whose scene happens earlier in the story.

    For example the shattered lantern (SC09) cut before the intact one (SC02)
    implies events in the wrong order. A WARNING, because trailers may
    reorder deliberately; an editor must confirm it does not mislead.
    """

    rule_id = "CONTINUITY_PROP_ORDER_001"

    def evaluate(
        self, trailer: TrailerCandidate, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        shown = [
            (clip.clip_id, evidence.scenes[clip.scene_id]) for clip in trailer.clips if clip.scene_id in evidence.scenes
        ]
        violations = []
        for index, (earlier_clip, earlier_scene) in enumerate(shown):
            for later_clip, later_scene in shown[index + 1 :]:
                if later_scene.sequence >= earlier_scene.sequence:
                    continue
                for prop_id in sorted(_visible_props(earlier_scene) & _visible_props(later_scene)):
                    violations.append(
                        ConstraintViolation(
                            rule_id=self.rule_id,
                            severity=ConstraintSeverity.WARNING,
                            entity_type=EntityType.PROP,
                            entity_id=prop_id,
                            reason_code=ReasonCode.CONTINUITY_ORDER_REVERSED,
                            message=f"{prop_id} appears in {earlier_clip} ({earlier_scene.scene_id}) and then in "
                            f"{later_clip} ({later_scene.scene_id}), which happens earlier in the story.",
                            remediation="Cut the clips in story order, or confirm the reordering does not mislead.",
                            clip_id=later_clip,
                            details={"first_cut": earlier_scene.scene_id, "second_cut": later_scene.scene_id},
                        )
                    )
        return violations


def _visible_props(scene: Scene) -> set[str]:
    return {appearance.prop_id for appearance in scene.props if appearance.state is not PropState.REFERENCED}
