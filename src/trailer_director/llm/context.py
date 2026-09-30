"""What the model is allowed to see: pre-filtered evidence, never the raw dataset.

The planning context contains the audience strategy, a spoiler-safe slice of
the story map, and only the evidence the constraint engine has already
marked eligible (plus short reasons for what is unavailable). Protected
reveals are named but their content is withheld. Free-text production notes,
continuity notes and the full synopsis are never sent. Lines and scene
summaries whose text reads like an instruction (``llm.screening``) are
withheld too; only their IDs are listed under ``withheld_text``.

The repair context adds the current plan and the repair decisions: which
clips may change, which actions are allowed, and what must be preserved.
"""

from typing import Any

from trailer_director.constraints import ConstraintSeverity
from trailer_director.domain import EpisodePackage, Scene
from trailer_director.domain.timecode import format_timecode
from trailer_director.llm.screening import is_instruction_like
from trailer_director.planning import AudienceStrategy, EvidencePool, PlanProposal
from trailer_director.repair import RepairRequest
from trailer_director.story import StoryMap


def planning_context(
    story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool, evidence: EpisodePackage
) -> dict[str, Any]:
    protected_lines = {d for reveal in story_map.protected_reveals for d in reveal.dialogue_ids}
    protected_scenes = {reveal.scene_id for reveal in story_map.protected_reveals}
    eligible_lines = [evidence.dialogue_line(d) for d in pool.eligible_dialogue_ids if d not in protected_lines]
    usable_lines = [line for line in eligible_lines if not is_instruction_like(line.text)]
    scene_ids = sorted({line.scene_id for line in usable_lines}, key=lambda s: evidence.scene(s).sequence)
    withheld = [line.dialogue_id for line in eligible_lines if is_instruction_like(line.text)]
    withheld += [s for s in scene_ids if is_instruction_like(evidence.scene(s).summary)]
    return {
        "episode": {"title": evidence.episode.title, "premise": story_map.premise.logline},
        "campaign": {
            "audience": strategy.audience,
            "evaluation_date": pool.context.evaluation_date.isoformat(),
            "territory": pool.context.territory,
        },
        "audience_strategy": {
            "positioning": strategy.positioning,
            "preferred_themes": strategy.preferred_themes,
            "preferred_tones": strategy.preferred_tones,
            "avoid": strategy.avoid,
            "pacing": strategy.pacing,
            "preferred_opening_scene_functions": strategy.hook_types,
            "target_duration_seconds": strategy.target_duration_seconds,
            "max_duration_seconds": strategy.max_duration_seconds,
        },
        "eligible_scenes": [_scene(evidence.scene(scene_id), pool, protected_scenes) for scene_id in scene_ids],
        "eligible_lines": [
            {
                "dialogue_id": line.dialogue_id,
                "scene_id": line.scene_id,
                "start": format_timecode(line.start),
                "end": format_timecode(line.end),
                "speaker": evidence.character(line.speaker_id).name,
                "text": line.text,
                "tone": line.tone,
                "importance": line.importance,
                "trailer_hook": line.dialogue_id in story_map.hook_dialogue_ids(),
                "review_needed": _warning_codes(pool, line.dialogue_id),
            }
            for line in usable_lines
        ],
        "eligible_music": [
            {
                "music_id": asset.music_id,
                "title": asset.title,
                "type": asset.type,
                "mood": asset.mood,
            }
            for asset in (evidence.music_asset(m) for m in pool.eligible_music_ids)
        ],
        "protected_reveals": [
            {"scene_id": reveal.scene_id, "spoiler_level": reveal.spoiler_level}
            for reveal in story_map.protected_reveals
        ],
        "unavailable": [
            {
                "entity_id": entry.entity_id,
                "reasons": sorted({v.reason_code for v in entry.violations if v.severity is ConstraintSeverity.ERROR}),
            }
            for entry in pool.rejected
        ],
        "withheld_text": withheld,
    }


def repair_context(request: RepairRequest, evidence: EpisodePackage) -> dict[str, Any]:
    context = planning_context(request.story_map, request.strategy, request.pool, evidence)
    context["current_plan"] = _plan_payload(request.proposal) if request.proposal else None
    context["repair_decisions"] = [
        {
            "clip_id": decision.clip_id,
            "scope": decision.scope,
            "because": decision.reason_code,
            "violations": [f"{ref.rule_id} on {ref.entity_type} {ref.entity_id}" for ref in decision.triggered_by],
            "preferred_action": decision.preferred_action,
            "allowed_actions": decision.allowed_actions,
            "preserve": decision.preserve,
        }
        for decision in request.decisions
    ]
    context["do_not_use"] = request.excluded_ids
    context["previous_output_problems"] = [f"{i.code} {i.location}: {i.message}" for i in request.planning_issues]
    return context


def _scene(scene: Scene, pool: EvidencePool, protected_scenes: set[str]) -> dict[str, Any]:
    # A scene that is only usable for tight cuts (rejected as a whole) or that holds a protected reveal can
    # have a summary paraphrasing lines the model must not see: send its bounds and labels, not the summary.
    shareable = (
        scene.scene_id in pool.eligible_scene_ids
        and scene.scene_id not in protected_scenes
        and not is_instruction_like(scene.summary)
    )
    return {
        "scene_id": scene.scene_id,
        "sequence": scene.sequence,
        "source_in": format_timecode(scene.source_in),
        "source_out": format_timecode(scene.source_out),
        "dramatic_function": scene.dramatic_function,
        "emotional_tone": scene.emotional_tone,
        "summary": scene.summary if shareable else None,
        "review_needed": _warning_codes(pool, scene.scene_id),
    }


def _warning_codes(pool: EvidencePool, entity_id: str) -> list[str]:
    return sorted({w.reason_code for w in pool.warnings_for(entity_id) if w.severity is ConstraintSeverity.WARNING})


def _plan_payload(proposal: PlanProposal) -> dict[str, Any]:
    rationales = {r.clip_id: r for r in proposal.clip_rationales}
    return {
        "audience": proposal.audience,
        "trailer_id": proposal.candidate.trailer_id,
        "title": proposal.title,
        "hook": proposal.hook,
        "positioning": proposal.positioning,
        "rationale": proposal.rationale,
        "clips": [
            {
                **clip.model_dump(mode="json"),
                "reason": rationales[clip.clip_id].reason,
                "evidence": rationales[clip.clip_id].evidence_ids,
            }
            for clip in proposal.candidate.clips
        ],
    }
