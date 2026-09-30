import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from trailer_director.cli_evaluate import add_evaluate_command, run_evaluate
from trailer_director.cli_export import add_export_command, run_export
from trailer_director.cli_models import add_models_command, run_models
from trailer_director.cli_planners import PlannerFactory, add_llm_arguments
from trailer_director.cli_repair import add_repair_commands, run_repair, run_replan
from trailer_director.constraints import ConstraintContext, ConstraintEngine, EligibilityResult
from trailer_director.data import ValidationReport, load_episode_package, load_raw_dataset, validate_dataset
from trailer_director.domain import AudienceType, EpisodePackage
from trailer_director.domain.edit import TrailerCandidate
from trailer_director.errors import DatasetLoadError, DatasetValidationError, PlannerError
from trailer_director.planning import PlannerMode, PlanningOutcome, run_planning
from trailer_director.planning.pipeline import campaign_context

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_LOAD_ERROR = 2

_SEVERITY_LABEL_WIDTH = 7
DEFAULT_REPLAY_DIR = Path("examples/planner_runs")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "evaluate-candidate":
        return _evaluate_candidate(args)
    if args.command == "plan":
        return _plan(args)
    if args.command == "repair":
        return run_repair(args)
    if args.command == "replan":
        return run_replan(args)
    if args.command == "evaluate":
        return run_evaluate(args)
    if args.command == "export":
        return run_export(args)
    if args.command == "models":
        return run_models(args)
    return _validate_data(args.data_dir)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trailer-director")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    dataset = argparse.ArgumentParser(add_help=False)
    dataset.add_argument("--data-dir", type=Path, default=Path("data"), help="dataset root (default: ./data)")

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-data", parents=[dataset], help="validate the episode evidence dataset")

    evaluate = commands.add_parser(
        "evaluate-candidate",
        parents=[dataset],
        help="check a trailer candidate, scene, line or music asset against the constraint rules",
    )
    subject = evaluate.add_mutually_exclusive_group(required=True)
    subject.add_argument("--candidate", type=Path, help="trailer candidate JSON file")
    subject.add_argument("--scene", help="evaluate a whole scene, e.g. SC10")
    subject.add_argument("--dialogue", help="evaluate one dialogue line, e.g. DLG_040")
    subject.add_argument("--music", help="evaluate a music asset, e.g. MUS_03")
    evaluate.add_argument("--audience", required=True, choices=[audience.value for audience in AudienceType])
    evaluate.add_argument(
        "--date", type=date.fromisoformat, help="date the promo would air, YYYY-MM-DD (default: episode release date)"
    )
    evaluate.add_argument("--territory", default="IN", help="territory code, e.g. IN or IN-HR (default: IN)")
    evaluate.add_argument("--json", action="store_true", help="print the result as JSON")

    plan = commands.add_parser(
        "plan", parents=[dataset], help="plan a trailer with the mock, replay or llm planner and check it"
    )
    plan.add_argument("--audience", required=True, choices=[audience.value for audience in AudienceType])
    plan.add_argument("--mode", choices=[mode.value for mode in PlannerMode], default=PlannerMode.MOCK.value)
    plan.add_argument(
        "--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR, help=f"replay files (default: {DEFAULT_REPLAY_DIR})"
    )
    plan.add_argument(
        "--date", type=date.fromisoformat, help="date the promo would air, YYYY-MM-DD (default: episode release date)"
    )
    plan.add_argument(
        "--territory", help="territory code (default: the episode region for dialect_region, otherwise its country)"
    )
    plan.add_argument("--json", action="store_true", help="print the planning run as JSON")
    add_llm_arguments(plan)
    add_repair_commands(commands, dataset)
    add_evaluate_command(commands, dataset)
    add_export_command(commands, dataset)
    add_models_command(commands, dataset)
    return parser


def _validate_data(data_dir: Path) -> int:
    try:
        raw = load_raw_dataset(data_dir)
    except DatasetLoadError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR

    package, report = validate_dataset(raw)
    for issue in report.issues:
        print(issue)
    if package is None:
        print(f"Dataset INVALID: {_counts(report)}")
        return EXIT_INVALID
    print(f"Dataset valid: {_summary(package)} ({_counts(report)})")
    return EXIT_OK


def _evaluate_candidate(args: argparse.Namespace) -> int:
    try:
        package = load_episode_package(args.data_dir)
        context = ConstraintContext(
            audience=args.audience,
            evaluation_date=args.date or package.episode.release_date,
            territory=args.territory,
        )
        trailer = _read_candidate(args.candidate) if args.candidate else None
    except (DatasetLoadError, DatasetValidationError, OSError, ValueError) as exc:
        # pydantic.ValidationError and json.JSONDecodeError are ValueErrors.
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR

    engine = ConstraintEngine(package)
    if trailer is not None:
        result = engine.evaluate_trailer(trailer, context)
    elif args.scene:
        result = engine.evaluate_scene(args.scene, context)
    elif args.dialogue:
        result = engine.evaluate_dialogue(args.dialogue, context)
    else:
        result = engine.evaluate_music(args.music, context)

    if args.json:
        print(json.dumps(result.model_dump(mode="json", exclude_none=True), indent=2))
    else:
        print(_format_result(result))
    return EXIT_OK if result.eligible else EXIT_INVALID


def _plan(args: argparse.Namespace) -> int:
    try:
        package = load_episode_package(args.data_dir)
        context = campaign_context(package, AudienceType(args.audience), args.date, args.territory)
    except (DatasetLoadError, DatasetValidationError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR

    try:
        planner = PlannerFactory(package, args).planner(args.mode, args.replay_dir)
    except PlannerError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR
    outcome = run_planning(package, ConstraintEngine(package), planner, context)
    if args.json:
        print(outcome.run.model_dump_json(indent=2, exclude_none=True))
    else:
        print(_format_outcome(outcome))
    return EXIT_OK if outcome.run.eligibility and outcome.run.eligibility.eligible else EXIT_INVALID


def _format_outcome(outcome: PlanningOutcome) -> str:
    run, story_map, strategy, pool = outcome.run, outcome.story_map, outcome.strategy, outcome.pool
    lines = [
        f"Story map generated: {story_map.method}, {len(story_map.events)} events, "
        f"{len(story_map.conflicts)} conflicts, {len(story_map.protected_reveals)} protected reveals, "
        f"{len(story_map.trailer_hooks)} trailer hooks (evidence {story_map.evidence_fingerprint})",
        f"Audience strategy generated: {strategy.audience} | hook types {', '.join(strategy.hook_types)} | "
        f"spoiler ceiling {strategy.max_spoiler_level} | target {strategy.target_duration_seconds}s",
        f"Eligible evidence: {len(pool.eligible_scene_ids)} scenes, {len(pool.eligible_dialogue_ids)} lines, "
        f"{len(pool.eligible_music_ids)} music; {len(pool.rejected)} items rejected with reasons",
        f"Planner mode: {run.mode} ({run.planner_version}), {run.model_calls} model call(s), "
        f"estimated cost {run.estimated_model_cost:.3f} {run.currency} (cost-sheet flat rate)",
    ]
    if run.usage is not None:
        served = f", served by {run.usage.model}" if run.usage.model else ""
        latency = f", {run.latency_seconds:.1f}s" if run.latency_seconds is not None else ""
        lines.append(f"Tokens: {run.usage.input_tokens} in, {run.usage.output_tokens} out{served}{latency}")
    for issue in run.issues:
        severity = f"{issue.severity.upper():<{_SEVERITY_LABEL_WIDTH}}"
        lines.append(f"  {severity} {issue.code} {issue.location}: {issue.message}")

    if run.proposal is None:
        lines.append(f"Candidate selected: none | run {run.run_id}: {run.status}")
        return "\n".join(lines)

    proposal = run.proposal
    lines.append(
        f'Candidate selected: {proposal.candidate.trailer_id} "{proposal.title}" - '
        f"{len(proposal.candidate.clips)} clips, {run.estimated_duration_seconds:g}s"
    )
    lines.append(f'  hook: "{proposal.hook}"')
    for clip, rationale in zip(proposal.candidate.clips, proposal.clip_rationales, strict=True):
        dump = clip.model_dump(mode="json")
        lines.append(
            f"  {clip.clip_id} {clip.scene_id} {dump['source_in']}-{dump['source_out']} "
            f"lines {', '.join(clip.dialogue_ids) or '-'} music {clip.music_id or '-'}"
        )
        lines.append(f"      why: {rationale.reason}")
        lines.append(f"      evidence: {', '.join(rationale.evidence_ids)}")
    if run.eligibility is not None:
        lines.append("Constraint result:")
        lines.append(_format_result(run.eligibility))
    lines.append(f"Run {run.run_id}: {run.status}")
    return "\n".join(lines)


def _read_candidate(path: Path) -> TrailerCandidate:
    try:
        return TrailerCandidate.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid trailer candidate\n{exc}") from None


def _format_result(result: EligibilityResult) -> str:
    context = result.context
    verdict = "ELIGIBLE" if result.eligible else "NOT ELIGIBLE"
    info_count = len(result.violations) - len(result.errors) - len(result.warnings)
    lines = [
        f"{result.subject_type} {result.subject_id} | audience={context.audience} "
        f"date={context.evaluation_date} territory={context.territory}",
        f"{verdict}: {len(result.errors)} error(s), {len(result.warnings)} warning(s), {info_count} info",
    ]
    for violation in sorted(result.violations, key=lambda v: -v.severity.rank):
        where = f" [{violation.clip_id}]" if violation.clip_id else ""
        lines.append(
            f"  {violation.severity.upper():<{_SEVERITY_LABEL_WIDTH}} {violation.rule_id}{where} "
            f"{violation.reason_code}: {violation.message}"
        )
        if violation.remediation:
            lines.append(f"  {'':<{_SEVERITY_LABEL_WIDTH}} fix: {violation.remediation}")
    return "\n".join(lines)


def _counts(report: ValidationReport) -> str:
    return f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"


def _summary(package: EpisodePackage) -> str:
    return (
        f"{package.episode.episode_id} '{package.episode.title}': "
        f"{len(package.scenes)} scenes, {len(package.dialogue)} dialogue lines, "
        f"{len(package.characters)} characters, {len(package.locations)} locations, "
        f"{len(package.props)} props, {len(package.music)} music assets, "
        f"{len(package.actor_rights)} actor rights, {len(package.rating_policies)} rating policies, "
        f"{len(package.audience_profiles)} audience profiles, "
        f"{len(package.historical_performance)} performance records"
    )
