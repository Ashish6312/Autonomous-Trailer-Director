"""Deterministic repair planner: carries out repair decisions with the mock planner's heuristics.

For each clip with a decision it tries the decision's allowed actions in
order - swap or remove music; re-cut the same scene around other eligible
lines; replace with another scene, preferring one with the same dramatic
function; drop. Clips without a decision are returned verbatim, and every
clip keeps its ``purpose``. It reuses ``score_scene``, ``choose_music`` and
the mock planner's clip builder rather than duplicating them.
"""

from typing import Any

from trailer_director.constraints import EntityType
from trailer_director.domain import SpoilerLevel
from trailer_director.domain.edit import TrailerClip
from trailer_director.errors import PlannerError
from trailer_director.planning import ClipRationale, EvidencePool, MockPlanner, PlannerResponse, PlanProposal
from trailer_director.planning.heuristics import choose_music, score_scene
from trailer_director.planning.mock import MIN_SCENE_SCORE
from trailer_director.repair.models import RepairAction, RepairDecision, RepairRequest, RepairScope

_FOOTAGE_SCOPES = (RepairScope.CLIP, RepairScope.DIALOGUE, RepairScope.TRAILER)


class DeterministicRepairPlanner(MockPlanner):
    version = "mock-repair-2"

    def repair(self, request: RepairRequest) -> PlannerResponse:
        if request.proposal is None:
            fresh = self.plan(request.story_map, request.strategy, request.pool)
            return fresh.model_copy(update={"planner_version": self.version})

        proposal = request.proposal
        decisions: dict[str, list[RepairDecision]] = {}
        for decision in request.decisions:
            decisions.setdefault(decision.clip_id, []).append(decision)
        rationales = {rationale.clip_id: rationale for rationale in proposal.clip_rationales}
        budget_ms = request.strategy.target_duration_seconds * 1000 - sum(
            clip.duration_ms for clip in proposal.candidate.clips if clip.clip_id not in decisions
        )
        used_scenes = {clip.scene_id for clip in proposal.candidate.clips}

        clips: list[dict[str, Any]] = []
        notes: list[str] = []
        for clip in proposal.candidate.clips:
            current = _clip_payload(clip, rationales[clip.clip_id])
            if clip.clip_id not in decisions:
                clips.append(current)
                continue
            repaired, note = self._apply(clip, current, decisions[clip.clip_id], request, used_scenes, budget_ms)
            notes.append(note)
            if repaired is not None:
                clips.append(repaired)
                used_scenes.add(repaired["scene_id"])
                budget_ms -= _duration_ms(repaired)

        if not clips:
            raise PlannerError("no eligible repair for any failing clip")
        clips.sort(key=lambda item: self._evidence.scene(item["scene_id"]).sequence)
        return PlannerResponse(
            mode=self.mode, planner_version=self.version, payload=self._revised(proposal, clips, notes, request)
        )

    def _apply(
        self,
        clip: TrailerClip,
        current: dict[str, Any],
        decisions: list[RepairDecision],
        request: RepairRequest,
        used_scenes: set[str],
        budget_ms: int,
    ) -> tuple[dict[str, Any] | None, str]:
        repaired, notes = current, []
        footage = next((d for d in decisions if d.scope in _FOOTAGE_SCOPES), None)
        if footage is not None:
            for action in footage.allowed_actions:
                if action is RepairAction.DROP_CLIP:
                    return None, f"{clip.clip_id} dropped ({footage.reason_code})"
                if action is RepairAction.RECUT_IN_SCENE:
                    attempt = self._recut(clip, footage, request, budget_ms)
                else:
                    attempt = self._replacement(clip, footage, request, used_scenes, budget_ms)
                if attempt is not None:
                    repaired = attempt
                    notes.append(f"{clip.clip_id} {action} ({footage.reason_code})")
                    break

        music = next((d for d in decisions if d.scope is RepairScope.MUSIC), None)
        if music is not None and repaired["scene_id"] == clip.scene_id:
            repaired = self._music_repair(repaired, music, request)
            notes.append(f"{clip.clip_id} music {clip.music_id} -> {repaired['music_id']} ({music.reason_code})")
        return repaired, "; ".join(notes) or f"{clip.clip_id} unchanged: no allowed repair available"

    def _music_repair(self, clip: dict[str, Any], decision: RepairDecision, request: RepairRequest) -> dict[str, Any]:
        rejected = {ref.entity_id for ref in decision.triggered_by} | set(request.excluded_ids)
        options = [music_id for music_id in request.pool.eligible_music_ids if music_id not in rejected]
        choice = choose_music(request.strategy, _with_music(request.pool, options), self._evidence)
        if choice is None:
            if RepairAction.REMOVE_MUSIC not in decision.allowed_actions:
                return clip
            new_music, why = None, "no eligible track, music removed"
        else:
            new_music, why = choice[0].music_id, f"now {choice[1]}"
        evidence = [e for e in clip["evidence"] if e != clip["music_id"]] + ([new_music] if new_music else [])
        reason = f"{clip['reason']}; repair ({decision.reason_code}): {clip['music_id']} replaced, {why}"
        return {**clip, "music_id": new_music, "reason": reason, "evidence": evidence}

    def _recut(
        self, clip: TrailerClip, decision: RepairDecision, request: RepairRequest, budget_ms: int
    ) -> dict[str, Any] | None:
        """Same scene, different lines: drop the offending lines and cut around the best remaining one."""
        if clip.scene_id not in self._evidence.scenes:
            return None
        offending = {ref.entity_id for ref in decision.triggered_by if ref.entity_type is EntityType.DIALOGUE}
        lines = self._candidate_lines(clip.scene_id, request, offending)
        if not lines:
            return None
        scene_function = next(sf for sf in request.story_map.scene_functions if sf.scene_id == clip.scene_id)
        score = score_scene(scene_function, request.strategy, request.pool)
        new, duration_ms = self._clip_for(
            self._evidence.scene(clip.scene_id), lines, request.strategy, request.story_map, score
        )
        if duration_ms > budget_ms or new["dialogue_ids"] == clip.dialogue_ids:
            return None
        return self._keep_identity(new, clip, f"Re-cut within {clip.scene_id} ({decision.reason_code})")

    def _replacement(
        self,
        clip: TrailerClip,
        decision: RepairDecision,
        request: RepairRequest,
        used_scenes: set[str],
        budget_ms: int,
    ) -> dict[str, Any] | None:
        """Best unused eligible scene that fits, preferring the failed scene's dramatic function."""
        failed_scene = self._evidence.scenes.get(clip.scene_id)
        wanted_function = failed_scene.dramatic_function if failed_scene else None
        excluded = set(request.excluded_ids)
        ranked = []
        for scene_function in request.story_map.scene_functions:
            scene_id = scene_function.scene_id
            if scene_id in used_scenes | excluded or scene_function.spoiler_level is SpoilerLevel.MAJOR:
                continue
            lines = self._candidate_lines(scene_id, request, set())
            score = score_scene(scene_function, request.strategy, request.pool)
            if lines and score.points >= MIN_SCENE_SCORE:
                same_function = scene_function.dramatic_function == wanted_function
                ranked.append((not same_function, -score.points, scene_function.sequence, scene_id, lines, score))

        for different_function, _, _, scene_id, lines, score in sorted(ranked, key=lambda item: item[:3]):
            new, duration_ms = self._clip_for(
                self._evidence.scene(scene_id), lines, request.strategy, request.story_map, score
            )
            if duration_ms > budget_ms:
                continue
            new["music_id"] = self._music_for_replacement(clip, decision, request)
            new["evidence"] = [*new["evidence"], *([new["music_id"]] if new["music_id"] else [])]
            match = "different dramatic function (none eligible)" if different_function else "same dramatic function"
            return self._keep_identity(new, clip, f"Replaces {clip.scene_id} ({decision.reason_code}; {match})")
        return None

    def _candidate_lines(self, scene_id: str, request: RepairRequest, offending: set[str]) -> list:
        protected = {d for reveal in request.story_map.protected_reveals for d in reveal.dialogue_ids}
        blocked = protected | offending | set(request.excluded_ids)
        return [
            line
            for line in self._evidence.lines_for_scene(scene_id)
            if request.pool.is_eligible(line.dialogue_id) and line.dialogue_id not in blocked
        ]

    def _music_for_replacement(self, clip: TrailerClip, decision: RepairDecision, request: RepairRequest) -> str | None:
        rejected = {
            r.entity_id for r in [*decision.triggered_by, *decision.also_resolves] if r.entity_type is EntityType.MUSIC
        }
        if clip.music_id and clip.music_id not in rejected and request.pool.is_eligible(clip.music_id):
            return clip.music_id
        options = [m for m in request.pool.eligible_music_ids if m not in rejected | set(request.excluded_ids)]
        choice = choose_music(request.strategy, _with_music(request.pool, options), self._evidence)
        return choice[0].music_id if choice else None

    @staticmethod
    def _keep_identity(new: dict[str, Any], old: TrailerClip, prefix: str) -> dict[str, Any]:
        """The repaired clip keeps its slot (clip ID), purpose and, unless replaced, its music."""
        new.setdefault("music_id", None)
        if new["music_id"] is None and new["scene_id"] == old.scene_id:
            new["music_id"] = old.music_id
            if old.music_id:
                new["evidence"].append(old.music_id)
        return {**new, "clip_id": old.clip_id, "purpose": old.purpose, "reason": f"{prefix}: {new['reason']}"}

    def _revised(
        self, proposal: PlanProposal, clips: list[dict[str, Any]], notes: list[str], request: RepairRequest
    ) -> dict[str, Any]:
        kept_lines = {dialogue_id for clip in clips for dialogue_id in clip["dialogue_ids"]}
        hook_line = next((line for line in self._evidence.dialogue.values() if line.text == proposal.hook), None)
        hook = proposal.hook
        if (hook_line is None or hook_line.dialogue_id not in kept_lines) and clips[0]["dialogue_ids"]:
            hook = self._evidence.dialogue_line(clips[0]["dialogue_ids"][0]).text
        return {
            "audience": proposal.audience,
            "trailer_id": proposal.candidate.trailer_id,
            "title": proposal.title,
            "hook": hook,
            "positioning": proposal.positioning,
            "rationale": f"{proposal.rationale} Repair {request.attempt}: {'; '.join(notes)}.",
            "clips": clips,
        }


def _clip_payload(clip: TrailerClip, rationale: ClipRationale) -> dict[str, Any]:
    return {**clip.model_dump(mode="json"), "reason": rationale.reason, "evidence": list(rationale.evidence_ids)}


def _with_music(pool: EvidencePool, music_ids: list[str]) -> EvidencePool:
    return pool.model_copy(update={"eligible_music_ids": music_ids})


def _duration_ms(clip: dict[str, Any]) -> int:
    return TrailerClip.model_validate({k: clip[k] for k in TrailerClip.model_fields if k in clip}).duration_ms
