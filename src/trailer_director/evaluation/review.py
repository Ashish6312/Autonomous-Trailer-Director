"""Human review sheet for the accepted plans.

Shows the automated results next to blank fields for what the verifier cannot
judge: audience fit, story relevance, cultural accuracy, hook, pacing. No
scores or rankings.
"""

from dataclasses import dataclass
from pathlib import Path

from trailer_director.constraints import ConstraintEngine
from trailer_director.domain import AudienceType, EpisodePackage
from trailer_director.planning import MockPlanner, Planner, ReplayPlanner
from trailer_director.planning.pipeline import campaign_context
from trailer_director.repair import DeterministicRepairPlanner, RepairLoop, RepairRun

REVIEW_CRITERIA = (
    ("Audience fit", "Does the cut speak to this audience's stated positioning, themes and tones?"),
    ("Story relevance", "Do the chosen moments represent what the episode is actually about?"),
    ("Narrative coherence", "Does the sequence make sense to someone who has not seen the episode?"),
    ("Dramatic-purpose preservation", "Does each clip do the job its stated purpose claims (after any repair)?"),
    (
        "Spoiler safety (perceived)",
        "Beyond the verified spoiler levels, does anything give away the reveal by implication?",
    ),
    (
        "Cultural / dialect appropriateness",
        "Are kinship terms, honorifics and customs shown accurately and respectfully? (regional reviewer)",
    ),
    ("Clarity of hook", "Is the hook line understandable and compelling on its own?"),
    ("Pacing / coverage", "Is the length and rhythm right, and is anything important missing?"),
)


@dataclass(frozen=True)
class ReviewItem:
    audience: AudienceType
    source: str
    run: RepairRun


def collect_plans(evidence: EpisodePackage, live_dir: Path | None) -> list[ReviewItem]:
    """The deterministic plan for every audience, plus every recorded live plan, each through full verification."""
    items = []
    for audience in AudienceType:
        sources: list[tuple[str, Planner]] = [("deterministic mock planner", MockPlanner(evidence))]
        if live_dir is not None and (live_dir / "planner_runs" / f"{audience}.json").exists():
            sources.insert(0, ("recorded live Claude plan (replayed)", ReplayPlanner(live_dir / "planner_runs")))
        for source, planner in sources:
            loop = RepairLoop(evidence, ConstraintEngine(evidence), DeterministicRepairPlanner(evidence))
            items.append(ReviewItem(audience, source, loop.run(planner, campaign_context(evidence, audience))))
    return items


def render_review_sheet(evidence: EpisodePackage, items: list[ReviewItem]) -> str:
    lines = [
        f"# Human creative review - {evidence.episode.title} ({evidence.episode.episode_id})",
        "",
        "Automated verification has already checked every plan below: evidence IDs and timecodes, spoiler",
        "ceilings, content ratings, music and performer rights, contract clauses, trailer length and budget.",
        "It cannot judge creative quality or cultural authenticity. That is what this review is for.",
        "",
        "| Automated verification (done, see run record) | Human creative judgment (this sheet) |",
        "|---|---|",
        "| Is every clip allowed for this audience, date and territory? | Is it a good trailer for this audience? |",
        "| Are protected reveals kept out, by spoiler level? | Does anything spoil the reveal by implication? |",
        "| Which lines use regional address terms? | Are those terms used accurately and respectfully? |",
        "| Which subtitles need review (safety flag, reading speed)? | Do they read well and keep the meaning? |",
        "",
        "How to review: write short, specific observations. Do not score or rank plans or audiences against each",
        "other; judge each plan against its own audience strategy. Cultural and dialect questions need a",
        "reviewer who knows the region's language and customs.",
        "",
    ]
    for number, item in enumerate(items, start=1):
        lines += _plan_section(number, evidence, item)
    return "\n".join(lines).rstrip() + "\n"


def _plan_section(number: int, evidence: EpisodePackage, item: ReviewItem) -> list[str]:
    run = item.run
    lines = [f"## {number}. {item.audience} - {item.source}", ""]
    if run.final_proposal is None:
        return lines + [f"Not accepted by verification ({run.status}); nothing to review.", ""]
    proposal = run.final_proposal
    first = run.attempts[0]
    review_items = sorted(
        {f"{w.reason_code} ({w.entity_id})" for w in run.final_eligibility.warnings} if run.final_eligibility else set()
    )
    lines += [
        f"- Planner: {first.planner_mode} `{first.planner_version}`; evidence `{run.evidence_fingerprint}`; "
        f"context {run.context.evaluation_date} {run.context.territory}",
        f"- Automated result: **{run.status}** ({run.summary})",
        f"- Flagged for human review by rules: {', '.join(review_items) or 'none'}",
        f"- Duration: {proposal.candidate.duration_ms / 1000:g}s",
        f'- Title: "{proposal.title}"',
        f'- Hook: "{proposal.hook}"',
        f"- Positioning: {proposal.positioning}",
        "",
        "| Clip | Scene | Source | Dialogue | Music | Purpose |",
        "|---|---|---|---|---|---|",
    ]
    for clip in proposal.candidate.clips:
        dump = clip.model_dump(mode="json")
        spoken = "<br>".join(f'{d}: "{evidence.dialogue_line(d).text}"' for d in clip.dialogue_ids) or "-"
        lines.append(
            f"| {clip.clip_id} | {clip.scene_id} ({evidence.scene(clip.scene_id).dramatic_function}) | "
            f"{dump['source_in']}-{dump['source_out']} | {spoken} | {clip.music_id or '-'} | {clip.purpose} |"
        )
    lines += ["", "| Criterion | Question | Observations |", "|---|---|---|"]
    lines += [f"| {name} | {question} | |" for name, question in REVIEW_CRITERIA]
    lines += [
        "",
        "Reviewer: ______  Date: ______  Decision: approve / approve with changes / send back",
        "",
        "Reviewer notes:",
        "",
    ]
    return lines
