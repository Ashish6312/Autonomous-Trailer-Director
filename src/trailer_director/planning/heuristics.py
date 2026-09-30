"""Scoring used by the mock planner.

These are transparent planning heuristics for a deterministic stand-in
planner, not a model of real audience behaviour. Every point awarded comes
with a reason and the evidence IDs behind it, so a plan can explain itself.
"""

from dataclasses import dataclass, field

from trailer_director.constraints import EntityType
from trailer_director.domain import Dialogue, EpisodePackage, Importance, MusicAsset, MusicType, language
from trailer_director.planning.pool import EvidencePool
from trailer_director.planning.strategy import AudienceStrategy
from trailer_director.story import StoryMap
from trailer_director.story.models import SceneFunction

HOOK_TYPE_POINTS = (3.0, 2.0, 1.0)
IMPORTANCE_POINTS = {Importance.HIGH: 2.0, Importance.MEDIUM: 1.0, Importance.LOW: 0.0}
TONE_MATCH_POINTS = 1.0
TRAILER_HOOK_POINTS = 2.0
REGIONAL_TERM_POINTS = 2.0
WARNING_PENALTY = 2.0
SUBTITLE_PENALTY = 1.0
ENGAGEMENT_DIVISOR = 50.0
MUSIC_TYPE_PREFERENCE = (MusicType.SONG, MusicType.SCORE, MusicType.STINGER)


@dataclass
class Score:
    points: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    def add(self, points: float, reason: str, *evidence_ids: str) -> None:
        self.points += points
        self.reasons.append(reason)
        self.evidence_ids.extend(entity_id for entity_id in evidence_ids if entity_id not in self.evidence_ids)


def address_terms(evidence: EpisodePackage) -> dict[str, str]:
    """Regional address terms (Kaka, Chacha, Jiji, ...) from character aliases, mapped to the character."""
    return language.address_terms(evidence.characters.values())


def score_scene(scene: SceneFunction, strategy: AudienceStrategy, pool: EvidencePool) -> Score:
    score = Score(evidence_ids=[scene.scene_id])
    if scene.dramatic_function in strategy.hook_types:
        rank = strategy.hook_types.index(scene.dramatic_function)
        score.add(
            HOOK_TYPE_POINTS[rank],
            f"{scene.scene_id} has dramatic function '{scene.dramatic_function}', "
            f"hook type #{rank + 1} for {strategy.audience}",
        )
    matched = sorted({tone for tone in scene.emotional_tone if tone.lower() in strategy.tone_words})
    if matched:
        score.add(
            TONE_MATCH_POINTS * len(matched), f"{scene.scene_id} tone {', '.join(matched)} matches the preferred tones"
        )
    score.add(IMPORTANCE_POINTS[scene.importance], f"{scene.scene_id} is a {scene.importance}-importance scene")

    signals = [s for s in scene.performance if s.audience is strategy.audience]
    if signals:
        best = max(signals, key=lambda s: s.engagement_score)
        score.add(
            best.engagement_score / ENGAGEMENT_DIVISOR,
            f"{best.campaign_id} engagement {best.engagement_score:g} with this audience (supporting evidence only)",
            best.campaign_id,
        )
    # Line-level warnings are weighed when lines are chosen; only footage-level ones lower the scene.
    warnings = [w for w in pool.warnings_for(scene.scene_id) if w.entity_type is not EntityType.DIALOGUE]
    if warnings:
        codes = ", ".join(sorted({w.reason_code for w in warnings}))
        score.add(-WARNING_PENALTY, f"penalised for constraint warnings ({codes})")
    return score


def score_line(line: Dialogue, strategy: AudienceStrategy, story_map: StoryMap, terms: dict[str, str]) -> Score:
    score = Score(evidence_ids=[line.dialogue_id])
    score.add(IMPORTANCE_POINTS[line.importance], f"{line.dialogue_id} is a {line.importance}-importance line")
    if line.dialogue_id in story_map.hook_dialogue_ids():
        score.add(TRAILER_HOOK_POINTS, f'{line.dialogue_id} is a story-map trailer hook: "{line.text}"')
    if set(line.tone.lower().split()) & strategy.tone_words:
        score.add(TONE_MATCH_POINTS, f"line tone '{line.tone}' matches the preferred tones")
    if strategy.dialect_review_required:
        used = language.terms_in_text(line.text, terms)
        if used:
            score.add(
                REGIONAL_TERM_POINTS,
                f"keeps the regional address term {', '.join(repr(t) for t in used)}",
                *(terms[t] for t in used),
            )
    elif strategy.subtitles_required and not line.subtitle_safe:
        score.add(-SUBTITLE_PENALTY, f"{line.dialogue_id} is not subtitle-safe for this audience")
    return score


def choose_music(
    strategy: AudienceStrategy, pool: EvidencePool, evidence: EpisodePackage
) -> tuple[MusicAsset, str] | None:
    """Eligible track whose mood best matches the strategy's tones and themes."""
    wanted = strategy.tone_words | {word for theme in strategy.preferred_themes for word in theme.lower().split()}
    candidates = [evidence.music_asset(music_id) for music_id in pool.eligible_music_ids]
    if not candidates:
        return None

    def rank(asset: MusicAsset) -> tuple[int, int, str]:
        overlap = len({mood.lower() for mood in asset.mood} & wanted)
        return (-overlap, MUSIC_TYPE_PREFERENCE.index(asset.type), asset.music_id)

    best = min(candidates, key=rank)
    matched = sorted({mood for mood in best.mood if mood.lower() in wanted})
    reason = f"mood {', '.join(matched)} matches the strategy" if matched else "no mood match; first eligible track"
    return best, f"{best.music_id} '{best.title}': {reason}"
