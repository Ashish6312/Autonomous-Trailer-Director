"""Kinship words used as a form of address must match the recorded relationships.

Catches localisation errors such as subtitling the honorific "Kaka" (Bansi,
not a relative) as "Uncle".
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
from trailer_director.domain.relations import ADDRESS_KINDS, kinship_addresses, relation_kind


class RelationshipTruthRule:
    rule_id = "STORY_TRUTH_RELATIONSHIP_001"

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]:
        scene = evidence.scenes.get(clip.scene_id)
        if scene is None:
            return []
        violations = []
        for line in lines_in_clip(clip, evidence):
            speaker = evidence.characters.get(line.speaker_id)
            if speaker is None:
                continue
            for name, word in kinship_addresses(line.text):
                addressees = _addressees(name, scene.characters, speaker.character_id, evidence)
                kinds = {other: relation_kind(speaker, other) for other in addressees}
                if not addressees or ADDRESS_KINDS[word] in kinds.values():
                    continue
                recorded = ", ".join(f"{other} is {kind or 'unrelated'}" for other, kind in sorted(kinds.items()))
                violations.append(
                    ConstraintViolation(
                        rule_id=self.rule_id,
                        severity=ConstraintSeverity.ERROR,
                        entity_type=EntityType.DIALOGUE,
                        entity_id=line.dialogue_id,
                        reason_code=ReasonCode.MISLEADING_RELATIONSHIP,
                        message=f"Dialogue {line.dialogue_id} has {speaker.name} address someone as '{word}', but "
                        f"the evidence records no such relationship ({recorded}).",
                        remediation="Keep the original address term, or correct the subtitle; do not imply kinship.",
                        clip_id=clip.clip_id,
                        details={"term": word, "speaker": speaker.character_id},
                    )
                )
        return violations


def _addressees(name: str | None, cast: list[str], speaker_id: str, evidence: EpisodePackage) -> list[str]:
    """The named character if the address names one, otherwise everyone else in the scene."""
    others = [c for c in cast if c != speaker_id and c in evidence.characters]
    if name:
        named = [c for c in others if evidence.characters[c].name.split()[0].casefold() == name.casefold()]
        if named:
            return named
    return others
