"""Exported artifacts: story, spoiler and constraint maps, one edit decision list per audience, a report.

Each trailer is planned from a recorded live response in ``live_dir`` when one
exists, otherwise by the mock planner, and goes through the full repair loop.
The clock is fixed, so the output is byte-reproducible.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trailer_director.constraints import ConstraintEngine, ConstraintSeverity, ReasonCode
from trailer_director.constraints.catalog import PIPELINE_CHECKS, RULE_CATALOG
from trailer_director.domain import AudienceType, EpisodePackage
from trailer_director.domain.timecode import format_timecode
from trailer_director.edit_fields import check_edit_fields, derive_edit_fields
from trailer_director.planning import (
    MockPlanner,
    Planner,
    ReplayPlanner,
    build_audience_strategy,
    build_evidence_pool,
)
from trailer_director.planning.claims import hook_sources
from trailer_director.planning.pipeline import campaign_context
from trailer_director.repair import (
    DeterministicRepairPlanner,
    RepairLoop,
    RepairPlanner,
    RepairRun,
    ReplayRepairPlanner,
)
from trailer_director.repair.strategy import NON_BLOCKING, STRATEGIES
from trailer_director.story import build_story_map

FIXED_NOW = datetime(2026, 9, 30, tzinfo=UTC)

_CULTURAL = {ReasonCode.DIALECT_REVIEW_REQUIRED, ReasonCode.SUBTITLE_REVIEW_REQUIRED}
_LEGAL = {ReasonCode.APPROVAL_REQUIRED}
_EDITORIAL = {
    ReasonCode.CONTINUITY_ORDER_REVERSED,
    ReasonCode.MISLEADING_SCENE_CONTEXT,
    ReasonCode.UNDECLARED_DIALOGUE_IN_CLIP,
    ReasonCode.SUBTITLE_READING_SPEED_HIGH,
}


@dataclass(frozen=True)
class TrailerResult:
    audience: AudienceType
    source: str
    run: RepairRun
    edl: dict[str, Any]


def story_map_artifact(evidence: EpisodePackage) -> dict[str, Any]:
    story_map = build_story_map(evidence)
    return {
        "evidence_fingerprint": story_map.evidence_fingerprint,
        "story_map": story_map.model_dump(mode="json"),
        "sensitive_content": [
            {"scene_id": scene.scene_id, **flag.model_dump(mode="json")}
            for scene in evidence.scenes_in_order()
            for flag in scene.sensitive_content
        ],
    }


def spoiler_map_artifact(evidence: EpisodePackage, engine: ConstraintEngine) -> dict[str, Any]:
    """Every scene and every line with a spoiler level, and whether each audience may use it on spoiler grounds."""
    story_map = build_story_map(evidence)
    protected = {r.scene_id for r in story_map.protected_reveals}
    contexts = {audience: campaign_context(evidence, audience) for audience in AudienceType}

    def verdicts(evaluate, entity_id: str) -> dict[str, str]:
        result = {}
        for audience, context in contexts.items():
            spoilers = [
                v for v in evaluate(entity_id, context).errors if v.reason_code is ReasonCode.SPOILER_LEVEL_EXCEEDED
            ]
            result[audience] = "blocked (SPOILER_LEVEL_EXCEEDED)" if spoilers else "allowed"
        return result

    return {
        "evidence_fingerprint": story_map.evidence_fingerprint,
        "audience_ceilings": {a: evidence.rating_policy(a).max_spoiler_level for a in AudienceType},
        "protected_reveals": [r.model_dump(mode="json") for r in story_map.protected_reveals],
        "scenes": [
            {
                "scene_id": scene.scene_id,
                "spoiler_level": scene.spoiler_level,
                "protected_reveal": scene.scene_id in protected,
                "audiences": verdicts(engine.evaluate_scene, scene.scene_id),
            }
            for scene in evidence.scenes_in_order()
        ],
        "lines": [
            {
                "dialogue_id": line.dialogue_id,
                "scene_id": line.scene_id,
                "spoiler_level": line.spoiler_level,
                "audiences": verdicts(engine.evaluate_dialogue, line.dialogue_id),
            }
            for line in sorted(evidence.dialogue.values(), key=lambda d: d.dialogue_id)
            if line.spoiler_level != "none"
        ],
    }


def constraint_map_artifact(evidence: EpisodePackage) -> dict[str, Any]:
    """The supplied rules as testable constraints: what is checked, against which evidence, and what happens."""
    return {
        "evidence_fingerprint": evidence.fingerprint(),
        "engine_rules": [
            {
                "rule_id": r.rule_id,
                "category": r.category,
                "checks": r.checks,
                "evidence": list(r.evidence),
                "reason_codes": list(r.reason_codes),
                "blocking_codes": [c for c in r.reason_codes if c not in NON_BLOCKING],
                "review_codes": [c for c in r.reason_codes if c in NON_BLOCKING and c != "HISTORICAL_PERFORMANCE"],
            }
            for r in RULE_CATALOG
        ],
        "pipeline_checks": [{"component": c, "category": k, "checks": d} for c, k, d in PIPELINE_CHECKS],
        "audience_policies": {
            audience: {
                **evidence.rating_policy(audience).model_dump(mode="json", exclude={"audience"}),
                "target_trailer_duration_seconds": evidence.audience_profile(audience).target_trailer_duration_seconds,
            }
            for audience in AudienceType
        },
        "music_rights": [m.model_dump(mode="json") for _, m in sorted(evidence.music.items())],
        "actor_rights": [a.model_dump(mode="json") for _, a in sorted(evidence.actor_rights.items())],
        "budget": evidence.cost_sheet.model_dump(mode="json", include={"currency", "limits", "fallback_strategy"}),
        "repair_strategies": [
            {"reason_code": code, "entity": entity, "scope": s.scope, "allowed_actions": list(s.actions)}
            for (code, entity), s in STRATEGIES.items()
        ],
        "human_review_codes": sorted(NON_BLOCKING - {ReasonCode.HISTORICAL_PERFORMANCE}),
    }


def build_trailer(evidence: EpisodePackage, audience: AudienceType, live_dir: Path | None) -> TrailerResult:
    planner, repairer, source = _sources(evidence, audience, live_dir)
    loop = RepairLoop(evidence, ConstraintEngine(evidence), repairer, sleep=lambda _: None)
    run = loop.run(planner, campaign_context(evidence, audience), FIXED_NOW)
    fallback = RepairLoop(evidence, ConstraintEngine(evidence), DeterministicRepairPlanner(evidence)).run(
        MockPlanner(evidence), campaign_context(evidence, audience), FIXED_NOW
    )
    return TrailerResult(audience, source, run, _edl(evidence, audience, source, run, fallback))


def validation_report(
    evidence: EpisodePackage, trailers: list[TrailerResult], evaluation: dict[str, Any] | None
) -> str:
    lines = [
        f"# Validation report - {evidence.episode.title} ({evidence.episode.episode_id})",
        "",
        f"Evidence fingerprint `{evidence.fingerprint()}`. Generated by `trailer-director export`; every trailer "
        "below passed through the full planner -> verifier -> repair loop. Verdicts come from the deterministic "
        "constraint engine, never from the planner.",
        "",
        "| Audience | Planner source | Verdict | Duration / target | Clips (scene) | Findings for review |",
        "|---|---|---|---|---|---|",
    ]
    for t in trailers:
        edl = t.edl
        clips = ", ".join(f"{s['clip_id']} ({s['scene_id']})" for s in edl["segments"])
        review = ", ".join(sorted({f["reason_code"] for s in edl["segments"] for f in s["risk_flags"]})) or "none"
        lines.append(
            f"| {t.audience} | {t.source} | **{edl['verdict']}** | {edl['duration_seconds']:g}s / "
            f"{edl['target_duration_seconds']}s | {clips} | {review} |"
        )
    lines += ["", "## Audience promises", ""]
    for t in trailers:
        lines += [
            f'- **{t.audience}**: {t.edl["audience_promise"]} Hook: "{t.edl["hook"]["text"]}" '
            f"({', '.join(t.edl['hook']['source_dialogue_ids']) or 'not found'})."
        ]
    lines += ["", "## Distinctness", "", "Shared material between trailers (same scene or same line):", ""]
    for i, a in enumerate(trailers):
        for b in trailers[i + 1 :]:
            scenes = sorted(_scenes(a) & _scenes(b))
            shared_lines = sorted(_lines(a) & _lines(b))
            lines.append(
                f"- {a.audience} / {b.audience}: scenes {', '.join(scenes) or 'none'}; "
                f"lines {', '.join(shared_lines) or 'none'}"
            )
    lines += ["", "## Segments", ""]
    for t in trailers:
        lines += [f"### {t.audience}", "", "| Clip | Source | Lines | Music | Risk flags |", "|---|---|---|---|---|"]
        for s in t.edl["segments"]:
            flags = "; ".join(f"{f['reason_code']} ({f['entity_id']})" for f in s["risk_flags"]) or "-"
            music = s["audio"]["music"]["music_id"] if s["audio"]["music"] else "-"
            dialogue = ", ".join(d["dialogue_id"] for d in s["audio"]["dialogue"]) or "-"
            where = f"{s['scene_id']} {s['source_in']}-{s['source_out']}"
            lines.append(f"| {s['clip_id']} | {where} | {dialogue} | {music} | {flags} |")
        lines += [
            "",
            "Human approvals: " + "; ".join(f"{a['role']} ({a['reason']})" for a in t.edl["human_approvals"]),
            "",
            "Transitions (suggested, not rendered): "
            + ", ".join(f"{x['from_clip']}->{x['to_clip']} {x['type']}" for x in t.edl["transitions"])
            + f". Voice-over: {len(t.edl['voice_over'])} lines. Text cards: "
            + (
                "; ".join(f'"{c["text"]}" ({", ".join(c["source_dialogue_ids"])})' for c in t.edl["text_cards"])
                or "none"
            )
            + f". Edit-field check: {t.edl['validation']['edit_fields']['status']}.",
            "",
        ]
    lines += ["## Checks applied", ""]
    lines += [f"- `{r.rule_id}` ({r.category}): {r.checks}" for r in RULE_CATALOG]
    lines += [f"- `{c}` ({k}): {d}" for c, k, d in PIPELINE_CHECKS]
    if evaluation is not None:
        lines += _evaluation_section(evaluation)
    return "\n".join(lines) + "\n"


def write_artifacts(
    out_dir: Path, evidence: EpisodePackage, live_dir: Path | None, evaluation: dict[str, Any] | None
) -> list[Path]:
    engine = ConstraintEngine(evidence)
    trailers = [build_trailer(evidence, audience, live_dir) for audience in AudienceType]
    files: dict[str, str] = {
        "story_map.json": _json(story_map_artifact(evidence)),
        "spoiler_map.json": _json(spoiler_map_artifact(evidence, engine)),
        "constraint_map.json": _json(constraint_map_artifact(evidence)),
        **{f"{t.audience}_trailer.json": _json(t.edl) for t in trailers},
        "validation_report.md": validation_report(evidence, trailers, evaluation),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def _sources(
    evidence: EpisodePackage, audience: AudienceType, live_dir: Path | None
) -> tuple[Planner, RepairPlanner, str]:
    repairer: RepairPlanner = DeterministicRepairPlanner(evidence)
    if live_dir is not None and (live_dir / "planner_runs" / f"{audience}.json").exists():
        if (live_dir / "repair_runs" / f"{audience}.json").exists():
            repairer = ReplayRepairPlanner(live_dir / "repair_runs")
        return ReplayPlanner(live_dir / "planner_runs"), repairer, "recorded live Claude plan (replayed)"
    return MockPlanner(evidence), repairer, "deterministic mock planner (no live run for this audience)"


def _edl(
    evidence: EpisodePackage, audience: AudienceType, source: str, run: RepairRun, fallback: RepairRun
) -> dict[str, Any]:
    strategy = build_audience_strategy(evidence, audience)
    first = run.attempts[0] if run.attempts else None
    proposal = run.final_proposal
    base = {
        "trailer_id": proposal.candidate.trailer_id if proposal else None,
        "audience": audience,
        "verdict": run.verdict,
        "status": run.status,
        "evidence_fingerprint": run.evidence_fingerprint,
        "context": run.context.model_dump(mode="json"),
        "planner": {
            "source": source,
            "mode": first.planner_mode if first else None,
            "version": first.planner_version if first else None,
            "served_by": first.usage.model if first and first.usage else None,
            "latency_seconds": first.latency_seconds if first else None,
        },
    }
    if proposal is None:
        return {**base, "summary": run.summary, "decision_log": _decision_log(run)}
    rationales = {r.clip_id: r for r in proposal.clip_rationales}
    warnings = run.final_eligibility.warnings if run.final_eligibility else []
    segments = [_segment(evidence, clip, rationales[clip.clip_id], warnings) for clip in proposal.candidate.clips]
    hook_ids = hook_sources(proposal.hook, evidence)
    clips = list(proposal.candidate.clips)
    fields = derive_edit_fields(clips, proposal.hook, hook_ids)
    pool = build_evidence_pool(ConstraintEngine(evidence), evidence, run.context)
    field_problems = check_edit_fields(fields, clips, proposal.hook, evidence, pool)
    return {
        **base,
        "title": proposal.title,
        "audience_promise": proposal.positioning,
        "hook": {"text": proposal.hook, "source_dialogue_ids": hook_ids},
        "rationale": proposal.rationale,
        "emotional_journey": [
            {
                "clip_id": s["clip_id"],
                "scene_id": s["scene_id"],
                "dramatic_function": evidence.scene(s["scene_id"]).dramatic_function,
                "emotional_tone": evidence.scene(s["scene_id"]).emotional_tone,
            }
            for s in segments
        ],
        "duration_seconds": proposal.candidate.duration_ms / 1000,
        "target_duration_seconds": strategy.target_duration_seconds,
        "max_duration_seconds": strategy.max_duration_seconds,
        "segments": segments,
        **fields.model_dump(mode="json"),
        "validation": {
            "verdict": run.verdict,
            "edit_fields": {"status": "REJECTED" if field_problems else "PASS", "problems": field_problems},
            "errors": len(run.final_eligibility.errors) if run.final_eligibility else None,
            "warnings": [_finding(w) for w in warnings],
            "info_count": sum(1 for v in run.final_eligibility.violations if v.severity is ConstraintSeverity.INFO)
            if run.final_eligibility
            else 0,
        },
        "decision_log": _decision_log(run),
        "human_approvals": _approvals(audience, warnings, strategy.withheld),
        "assumptions": _assumptions(source),
        "estimated_cost": {
            "model_calls": run.budget.model_calls,
            "estimated_cost": run.budget.estimated_cost,
            "currency": run.budget.currency,
            "input_tokens": run.budget.input_tokens or None,
            "output_tokens": run.budget.output_tokens or None,
            "note": "cost-sheet flat rate per model call, not billing; replay makes no call",
        },
        "lower_cost_fallback": {
            "planner": "deterministic mock planner + deterministic repair (0 model calls)",
            "verdict": fallback.verdict,
            "trailer_id": fallback.final_proposal.candidate.trailer_id if fallback.final_proposal else None,
            "duration_seconds": fallback.final_proposal.candidate.duration_ms / 1000
            if fallback.final_proposal
            else None,
            "scenes": [c.scene_id for c in fallback.final_proposal.candidate.clips] if fallback.final_proposal else [],
        },
    }


def _segment(evidence: EpisodePackage, clip, rationale, warnings) -> dict[str, Any]:
    scene = evidence.scene(clip.scene_id)
    dump = clip.model_dump(mode="json")
    lines = [evidence.dialogue_line(d) for d in clip.dialogue_ids]
    music = evidence.music_asset(clip.music_id) if clip.music_id else None
    return {
        "clip_id": clip.clip_id,
        "scene_id": clip.scene_id,
        "source_in": dump["source_in"],
        "source_out": dump["source_out"],
        "duration_seconds": clip.duration_ms / 1000,
        "purpose": clip.purpose,
        "video": {
            "scene_id": scene.scene_id,
            "location": evidence.locations[scene.location_id].name,
            "time_of_day": scene.time_of_day,
            "characters": [evidence.character(c).name for c in scene.characters],
            "visual_tags": scene.visual_tags,
        },
        "audio": {
            "dialogue": [
                {
                    "dialogue_id": line.dialogue_id,
                    "speaker": evidence.character(line.speaker_id).name,
                    "start": format_timecode(line.start),
                    "end": format_timecode(line.end),
                }
                for line in lines
            ],
            "music": {"music_id": music.music_id, "title": music.title, "mood": music.mood} if music else None,
        },
        "subtitles": [
            {
                "dialogue_id": line.dialogue_id,
                "text": line.text,
                "subtitle_safe": line.subtitle_safe,
                "chars_per_second": round(len(line.text) / ((line.end - line.start) / 1000), 1),
            }
            for line in lines
        ],
        "reason": rationale.reason,
        "evidence": rationale.evidence_ids,
        "risk_flags": [_finding(w) for w in warnings if w.clip_id == clip.clip_id],
    }


def _finding(violation) -> dict[str, Any]:
    return {
        "rule_id": violation.rule_id,
        "reason_code": violation.reason_code,
        "entity_id": violation.entity_id,
        "message": violation.message,
    }


def _decision_log(run: RepairRun) -> list[dict[str, Any]]:
    return [entry.model_dump(mode="json", exclude={"refs"}) for entry in run.audit_trail]


def _approvals(audience: AudienceType, warnings, withheld: list[str]) -> list[dict[str, str]]:
    codes = {w.reason_code for w in warnings}
    approvals = [{"role": "editorial", "reason": "final cut sign-off; metadata-only checks, no media inspected"}]
    if codes & _EDITORIAL:
        approvals.append({"role": "editorial", "reason": ", ".join(sorted(codes & _EDITORIAL))})
    approvals.append({"role": "legal", "reason": "confirm the rights and contract records used are current"})
    if codes & _LEGAL:
        approvals.append({"role": "legal", "reason": ", ".join(sorted(codes & _LEGAL))})
    cultural = sorted(codes & _CULTURAL)
    if audience is AudienceType.DIALECT_REGION or cultural or withheld:
        reasons = cultural + (["stereotyping preferences withheld from planning"] if withheld else [])
        approvals.append({"role": "cultural", "reason": ", ".join(reasons) or "regional audience: language and custom"})
    approvals.append({"role": "marketing", "reason": "title, audience promise and hook are marketing copy"})
    return approvals


def _assumptions(source: str) -> list[str]:
    return [
        "Eligibility is judged from scene and line metadata; no media file was inspected.",
        "Timecodes are episode-relative; there is no source-reel mapping.",
        "Subtitle text is the dialogue text field; reading speed uses the line's spoken duration.",
        "Rights and contract records in the dataset are assumed current on the evaluation date.",
        "Transitions and the hook text card are derived by rule, not by the planner; nothing is rendered.",
        f"Planner: {source}.",
    ]


def _evaluation_section(report: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Evaluation",
        "",
        f"{report['run_count']} runs over {report['scenario_count']} scenario IDs, each run twice from scratch. "
        f"Unexpected: {', '.join(report['unexpected']) or 'none'}. No aggregate score.",
        "",
        "| Scenario | Audience | Initial | Final | Reason codes (initial) | As designed | Reproducible |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        lines.append(
            f"| {r['scenario_id']} | {r['audience']} | {r['initial_status']} | {r['final_status']} | "
            f"{', '.join(r['reason_codes']) or '-'} | {'yes' if r['as_expected'] else 'NO'} | "
            f"{'yes' if r['replay_match'] else 'NO'} |"
        )
    lines += [""] + [f"- not applicable: {entry}" for entry in report["not_applicable"]]
    return lines


def _scenes(t: TrailerResult) -> set[str]:
    return {s["scene_id"] for s in t.edl.get("segments", [])}


def _lines(t: TrailerResult) -> set[str]:
    return {d["dialogue_id"] for s in t.edl.get("segments", []) for d in s["audio"]["dialogue"]}


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
