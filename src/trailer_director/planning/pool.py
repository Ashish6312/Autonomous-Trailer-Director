"""Eligible evidence pool: what the constraint engine allows for one campaign context, and why not the rest."""

from trailer_director.constraints import (
    ConstraintContext,
    ConstraintEngine,
    ConstraintSeverity,
    ConstraintViolation,
    EligibilityResult,
    EntityType,
)
from trailer_director.domain import EpisodePackage
from trailer_director.domain.base import DomainModel
from trailer_director.planning.models import PoolSummary


class PoolEntry(DomainModel):
    entity_type: EntityType
    entity_id: str
    violations: list[ConstraintViolation]


class EvidencePool(DomainModel):
    context: ConstraintContext
    eligible_scene_ids: list[str]
    eligible_dialogue_ids: list[str]
    eligible_music_ids: list[str]
    flagged: list[PoolEntry]
    """Eligible items that carry warnings a planner should weigh."""
    rejected: list[PoolEntry]
    """Ineligible items with the errors (and warnings) that rule them out."""

    def is_eligible(self, entity_id: str) -> bool:
        return entity_id in {*self.eligible_scene_ids, *self.eligible_dialogue_ids, *self.eligible_music_ids}

    def warnings_for(self, entity_id: str) -> list[ConstraintViolation]:
        return next((entry.violations for entry in self.flagged if entry.entity_id == entity_id), [])

    def rejection_for(self, entity_id: str) -> list[ConstraintViolation]:
        return next((entry.violations for entry in self.rejected if entry.entity_id == entity_id), [])

    def summary(self) -> PoolSummary:
        return PoolSummary(
            eligible_scenes=len(self.eligible_scene_ids),
            eligible_dialogue=len(self.eligible_dialogue_ids),
            eligible_music=len(self.eligible_music_ids),
            rejected=len(self.rejected),
        )


def build_evidence_pool(engine: ConstraintEngine, evidence: EpisodePackage, context: ConstraintContext) -> EvidencePool:
    """Evaluate every scene (whole), every line (cut tightly) and every music asset through the engine."""
    results = [engine.evaluate_scene(scene.scene_id, context) for scene in evidence.scenes_in_order()]
    results += [engine.evaluate_dialogue(dialogue_id, context) for dialogue_id in sorted(evidence.dialogue)]
    results += [engine.evaluate_music(music_id, context) for music_id in sorted(evidence.music)]

    def eligible_ids(entity_type: EntityType) -> list[str]:
        return [r.subject_id for r in results if r.subject_type is entity_type and r.eligible]

    return EvidencePool(
        context=context,
        eligible_scene_ids=eligible_ids(EntityType.SCENE),
        eligible_dialogue_ids=eligible_ids(EntityType.DIALOGUE),
        eligible_music_ids=eligible_ids(EntityType.MUSIC),
        flagged=[_entry(r) for r in results if r.eligible and r.warnings],
        rejected=[_entry(r) for r in results if not r.eligible],
    )


def _entry(result: EligibilityResult) -> PoolEntry:
    return PoolEntry(
        entity_type=result.subject_type,
        entity_id=result.subject_id,
        violations=[v for v in result.violations if v.severity is not ConstraintSeverity.INFO],
    )
