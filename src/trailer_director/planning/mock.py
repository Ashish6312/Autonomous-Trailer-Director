"""Deterministic stand-in planner.

Selection: rank scenes with ``score_scene`` and drop those below
``MIN_SCENE_SCORE`` (no real fit with the audience). In each remaining scene
take the best-scoring eligible line, plus the next line if it is eligible and
follows closely, to keep an exchange intact. Add clips in rank order until the
audience's target duration or ``MAX_CLIPS`` is reached. Clips are then put back in story order
so the trailer does not imply a different sequence of events.

The planner only draws from evidence the pool marks eligible and never from
protected reveals, but that is a courtesy: the constraint engine still
decides.
"""

from typing import Any

from trailer_director.domain import Dialogue, EpisodePackage, Scene, SpoilerLevel
from trailer_director.domain.timecode import format_timecode
from trailer_director.planning.heuristics import Score, address_terms, choose_music, score_line, score_scene
from trailer_director.planning.models import PlannerMode, PlannerResponse
from trailer_director.planning.pool import EvidencePool
from trailer_director.planning.strategy import AudienceStrategy
from trailer_director.story import StoryMap

MAX_CLIPS = 4
MIN_SCENE_SCORE = 3.0
CLIP_PADDING_MS = 500
MAX_PAIR_GAP_MS = 2000


class MockPlanner:
    mode = PlannerMode.MOCK
    version = "mock-heuristic-1"

    def __init__(self, evidence: EpisodePackage) -> None:
        self._evidence = evidence
        self._terms = address_terms(evidence)

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse:
        protected_lines = {d for reveal in story_map.protected_reveals for d in reveal.dialogue_ids}
        ranked: list[tuple[float, int, Scene, list[Dialogue], Score]] = []
        for scene_function in story_map.scene_functions:
            if scene_function.spoiler_level is SpoilerLevel.MAJOR:
                continue
            scene = self._evidence.scene(scene_function.scene_id)
            lines = [
                line
                for line in self._evidence.lines_for_scene(scene.scene_id)
                if pool.is_eligible(line.dialogue_id) and line.dialogue_id not in protected_lines
            ]
            score = score_scene(scene_function, strategy, pool)
            if lines and score.points >= MIN_SCENE_SCORE:
                ranked.append((-score.points, scene.sequence, scene, lines, score))
        ranked.sort(key=lambda item: (item[0], item[1]))

        music = choose_music(strategy, pool, self._evidence)
        target_ms = strategy.target_duration_seconds * 1000
        selected: list[tuple[Scene, dict[str, Any], Score]] = []
        total_ms = 0
        for _, _, scene, lines, scene_score in ranked:
            if len(selected) == MAX_CLIPS:
                break
            clip, clip_ms = self._clip_for(scene, lines, strategy, story_map, scene_score)
            if total_ms + clip_ms <= target_ms:
                selected.append((scene, clip, scene_score))
                total_ms += clip_ms

        lead_line = self._evidence.dialogue_line(selected[0][1]["dialogue_ids"][0]) if selected else None
        selected.sort(key=lambda item: item[0].sequence)
        clips = []
        for number, (_, clip, _) in enumerate(selected, start=1):
            clip["clip_id"] = f"CLIP_{number:03d}"
            if music is not None:
                clip["music_id"] = music[0].music_id
                clip["reason"] += f"; music {music[1]}"
                clip["evidence"].append(music[0].music_id)
            clips.append(clip)

        return PlannerResponse(
            mode=self.mode,
            planner_version=self.version,
            payload=self._payload(strategy, clips, lead_line),
        )

    def _clip_for(
        self, scene: Scene, lines: list[Dialogue], strategy: AudienceStrategy, story_map: StoryMap, scene_score: Score
    ) -> tuple[dict[str, Any], int]:
        scored = sorted(
            ((score_line(line, strategy, story_map, self._terms), line) for line in lines),
            key=lambda pair: (-pair[0].points, pair[1].start),
        )
        line_score, best = scored[0]
        chosen = [best, *self._following_eligible_line(scene, best, lines)]

        all_lines = self._evidence.lines_for_scene(scene.scene_id)
        before = [line.end for line in all_lines if line.end <= chosen[0].start]
        after = [line.start for line in all_lines if line.start >= chosen[-1].end]
        source_in = max([scene.source_in, chosen[0].start - CLIP_PADDING_MS, *before])
        source_out = min([scene.source_out, chosen[-1].end + CLIP_PADDING_MS, *after])

        reasons = [*scene_score.reasons, *line_score.reasons]
        evidence_ids = [*scene_score.evidence_ids, *(line.dialogue_id for line in chosen)]
        evidence_ids += [e for e in line_score.evidence_ids if e not in evidence_ids]
        clip = {
            "clip_id": "",
            "scene_id": scene.scene_id,
            "source_in": format_timecode(source_in),
            "source_out": format_timecode(source_out),
            "dialogue_ids": [line.dialogue_id for line in chosen],
            "music_id": None,
            "purpose": f"{scene.dramatic_function}_hook",
            "reason": "; ".join(reasons),
            "evidence": evidence_ids,
        }
        return clip, source_out - source_in

    def _following_eligible_line(self, scene: Scene, line: Dialogue, eligible: list[Dialogue]) -> list[Dialogue]:
        position = scene.dialogue_ids.index(line.dialogue_id)
        if position + 1 == len(scene.dialogue_ids):
            return []
        following = self._evidence.dialogue_line(scene.dialogue_ids[position + 1])
        close_enough = following.start - line.end <= MAX_PAIR_GAP_MS
        return [following] if following in eligible and close_enough else []

    def _payload(
        self, strategy: AudienceStrategy, clips: list[dict[str, Any]], lead_line: Dialogue | None
    ) -> dict[str, Any]:
        hook_types = ", ".join(strategy.hook_types)
        return {
            "audience": strategy.audience,
            "trailer_id": f"TRL_{strategy.audience.upper()}_MOCK",
            "title": f"{self._evidence.episode.title}: {strategy.display_name}",
            "hook": lead_line.text if lead_line else "",
            "positioning": strategy.positioning,
            "rationale": (
                f"Deterministic mock plan. Scenes ranked by {strategy.audience} hook types ({hook_types}), "
                f"tone match with ({', '.join(strategy.preferred_tones)}), importance and trailer-hook lines; "
                "only pool-eligible lines, no protected reveals; clips kept in story order."
            ),
            "clips": clips,
        }
