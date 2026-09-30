"""The rule interface and helpers shared by rule modules.

A rule is any object with a ``rule_id`` and an ``evaluate`` method that
returns zero or more violations. Rules never raise for bad input and never
stop other rules from running; missing evidence is reported by the source
rules and skipped by the rest.
"""

from typing import Protocol

from trailer_director.constraints.models import ConstraintContext, ConstraintViolation
from trailer_director.domain import Dialogue, EpisodePackage
from trailer_director.domain.edit import TrailerCandidate, TrailerClip


class ClipRule(Protocol):
    rule_id: str

    def evaluate(
        self, clip: TrailerClip, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]: ...


class TrailerRule(Protocol):
    rule_id: str

    def evaluate(
        self, trailer: TrailerCandidate, context: ConstraintContext, evidence: EpisodePackage
    ) -> list[ConstraintViolation]: ...


def lines_in_clip(clip: TrailerClip, evidence: EpisodePackage) -> list[Dialogue]:
    """Lines of the clip's scene that the clip declares or that are audible within its range."""
    scene = evidence.scenes.get(clip.scene_id)
    if scene is None:
        return []
    lines = (evidence.dialogue[dialogue_id] for dialogue_id in scene.dialogue_ids)
    return [
        line
        for line in lines
        if line.dialogue_id in clip.dialogue_ids or (line.start < clip.source_out and line.end > clip.source_in)
    ]
