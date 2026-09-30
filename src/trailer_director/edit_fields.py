"""Transitions, voice-over and text cards of a trailer plan: derivation by rule, and verification.

None of these come from the planner. Transitions are an editing suggestion
(nothing is rendered). Voice-over is empty because the episode package has
no voice-over script. The only text card is the hook, which is already
verified against the dialogue. Any text here must quote dialogue the
audience may use, so it cannot add story facts or spoilers.
"""

from typing import Any, Literal

from trailer_director.domain import EpisodePackage
from trailer_director.domain.base import DomainModel
from trailer_director.domain.edit import TrailerClip
from trailer_director.planning.claims import quote_status
from trailer_director.planning.pool import EvidencePool

TRANSITION_TYPES = ("cut", "dissolve", "fade")
MAX_CARD_CHARS_PER_SECOND = 17.0
MIN_CARD_SECONDS = 2.0


class Transition(DomainModel):
    from_clip: str
    to_clip: str
    type: str
    reason: str


class VoiceOverLine(DomainModel):
    text: str
    source_dialogue_ids: list[str]


class TextCard(DomainModel):
    card_id: str
    text: str
    source_dialogue_ids: list[str]
    basis: Literal["hook", "dialogue"]
    position: str = "after the last segment"
    duration_seconds: float


class EditFields(DomainModel):
    transitions: list[Transition] = []
    voice_over: list[VoiceOverLine] = []
    text_cards: list[TextCard] = []


def load_edit_fields(trailer: dict[str, Any]) -> EditFields:
    """Read the three fields from a trailer JSON; plans written before they existed get empty lists."""
    return EditFields.model_validate({name: trailer.get(name, []) for name in EditFields.model_fields})


def derive_edit_fields(clips: list[TrailerClip], hook: str, hook_sources: list[str]) -> EditFields:
    transitions = []
    for before, after in zip(clips, clips[1:], strict=False):
        same_scene = before.scene_id == after.scene_id
        transitions.append(
            Transition(
                from_clip=before.clip_id,
                to_clip=after.clip_id,
                type="cut" if same_scene else "dissolve",
                reason=f"continuous action within {before.scene_id}"
                if same_scene
                else f"scene change {before.scene_id} -> {after.scene_id}: marks the move in time and place",
            )
        )
    cards = []
    if hook_sources:
        # Shown as text so the hook does not depend on audio; long enough to read.
        seconds = round(max(MIN_CARD_SECONDS, len(hook) / MAX_CARD_CHARS_PER_SECOND), 1)
        cards.append(
            TextCard(
                card_id="CARD_HOOK", text=hook, source_dialogue_ids=hook_sources, basis="hook", duration_seconds=seconds
            )
        )
    return EditFields(transitions=transitions, voice_over=[], text_cards=cards)


def check_edit_fields(
    fields: EditFields, clips: list[TrailerClip], hook: str, evidence: EpisodePackage, pool: EvidencePool
) -> list[str]:
    """Problems that stop the fields from shipping; an empty list means they are valid."""
    problems = []
    order = [clip.clip_id for clip in clips]
    adjacent = set(zip(order, order[1:], strict=False))
    seen = set()
    for t in fields.transitions:
        pair = (t.from_clip, t.to_clip)
        if t.from_clip not in order or t.to_clip not in order:
            problems.append(f"transition {t.from_clip} -> {t.to_clip}: unknown clip")
        elif pair not in adjacent:
            problems.append(f"transition {t.from_clip} -> {t.to_clip}: clips are not consecutive in the timeline")
        elif pair in seen:
            problems.append(f"transition {t.from_clip} -> {t.to_clip}: duplicated")
        if t.type not in TRANSITION_TYPES:
            problems.append(f"transition {t.from_clip} -> {t.to_clip}: type '{t.type}' is not allowed")
        seen.add(pair)
    for index, line in enumerate(fields.voice_over):
        problems += _text_problems(f"voice_over[{index}]", line.text, line.source_dialogue_ids, evidence, pool)
    for card in fields.text_cards:
        problems += _text_problems(card.card_id, card.text, card.source_dialogue_ids, evidence, pool)
        if card.basis == "hook" and card.text != hook:
            problems.append(f"{card.card_id}: text differs from the verified hook")
        if card.duration_seconds < max(MIN_CARD_SECONDS, len(card.text) / MAX_CARD_CHARS_PER_SECOND) - 0.05:
            problems.append(f"{card.card_id}: too short to read ({card.duration_seconds}s)")
    return problems


def _text_problems(
    label: str, text: str, source_ids: list[str], evidence: EpisodePackage, pool: EvidencePool
) -> list[str]:
    problems = [f"{label}: unknown source '{d}'" for d in source_ids if d not in evidence.dialogue]
    if not source_ids:
        problems.append(f"{label}: no source dialogue cited")
    unsupported, ineligible = quote_status(text, evidence, pool)
    problems += [f'{label}: "{s}" is not in the episode dialogue' for s in unsupported]
    problems += [
        f'{label}: "{s}" quotes a line this audience may not use (spoiler, rating or rights)' for s in ineligible
    ]
    return problems
