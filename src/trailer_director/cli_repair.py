"""CLI commands for the repair loop: ``repair`` and ``replan``."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from trailer_director.cli_planners import PlannerFactory, add_llm_arguments
from trailer_director.config import ConfigError, run_limits
from trailer_director.constraints import ConstraintEngine
from trailer_director.data import load_episode_package
from trailer_director.domain import AudienceType, EpisodePackage
from trailer_director.errors import DatasetLoadError, DatasetValidationError, PlannerError
from trailer_director.planning import Planner, PlannerMode, ReplayPlanner
from trailer_director.planning.pipeline import campaign_context
from trailer_director.repair import (
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DeterministicRepairPlanner,
    LoopStatus,
    OutagePlanner,
    RepairLoop,
    RepairRun,
)

DEFAULT_PLANNER_REPLAY_DIR = Path("examples/planner_runs")
DEFAULT_REPAIR_REPLAY_DIR = Path("examples/repair_runs")
EXIT_OK = 0
EXIT_NOT_ACCEPTED = 1
EXIT_LOAD_ERROR = 2


def add_repair_commands(commands: argparse._SubParsersAction, dataset: argparse.ArgumentParser) -> None:
    repair = commands.add_parser(
        "repair", parents=[dataset], help="plan, verify and repair until eligible or out of attempts/budget"
    )
    _add_common(repair)
    repair.add_argument("--date", type=date.fromisoformat, help="campaign date (default: episode release date)")
    repair.add_argument(
        "--simulate-outage",
        type=int,
        default=0,
        metavar="N",
        help="make the planner fail its first N calls, to show retry and replay fallback",
    )

    replan = commands.add_parser(
        "replan", parents=[dataset], help="plan for one date, then re-verify and selectively repair for another"
    )
    _add_common(replan)
    replan.add_argument("--from-date", type=date.fromisoformat, required=True, help="date the plan was made for")
    replan.add_argument("--to-date", type=date.fromisoformat, help="new campaign date (default: release date)")


def run_repair(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return EXIT_LOAD_ERROR
    package, loop, factory = loaded
    try:
        planner: Planner = factory.planner(args.mode, args.replay_dir)
    except PlannerError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR
    if args.simulate_outage:
        planner = OutagePlanner(planner, args.simulate_outage)
    run = loop.run(planner, campaign_context(package, AudienceType(args.audience), args.date, args.territory))
    _print(run, args.json)
    return EXIT_OK if run.status is LoopStatus.ACCEPTED else EXIT_NOT_ACCEPTED


def run_replan(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return EXIT_LOAD_ERROR
    package, loop, factory = loaded
    audience = AudienceType(args.audience)
    try:
        planner = factory.planner(args.mode, args.replay_dir)
    except PlannerError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR
    original = loop.run(planner, campaign_context(package, audience, args.from_date, args.territory))
    if original.status is not LoopStatus.ACCEPTED:
        if not args.json:
            print(f"Plan for {args.from_date} was not accepted ({original.status}); nothing to replan.")
        _print(original, args.json)
        return EXIT_NOT_ACCEPTED
    run = loop.replan(original.final_proposal, campaign_context(package, audience, args.to_date, args.territory))
    if not args.json:
        new_date = run.context.evaluation_date
        print(f"Plan accepted for {args.from_date} ({original.run_id}); conditions changed to {new_date}.")
    _print(run, args.json)
    return EXIT_OK if run.status is LoopStatus.ACCEPTED else EXIT_NOT_ACCEPTED


def _add_common(parser: argparse.ArgumentParser) -> None:
    modes = [mode.value for mode in PlannerMode]
    parser.add_argument("--audience", required=True, choices=[audience.value for audience in AudienceType])
    parser.add_argument("--mode", choices=modes, default=PlannerMode.MOCK.value, help="initial planner")
    parser.add_argument("--repair-mode", choices=modes, default=PlannerMode.MOCK.value, help="repair planner")
    parser.add_argument("--replay-dir", type=Path, default=DEFAULT_PLANNER_REPLAY_DIR)
    parser.add_argument("--repair-replay-dir", type=Path, default=DEFAULT_REPAIR_REPLAY_DIR)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_REPAIR_ATTEMPTS, help="repair attempts")
    parser.add_argument("--territory", help="territory (default: IN-HR for dialect_region, otherwise IN)")
    parser.add_argument("--json", action="store_true", help="print the repair run as JSON")
    parser.add_argument(
        "--max-model-calls",
        type=int,
        help="cap model calls below the cost sheet (env TRAILER_DIRECTOR_MAX_MODEL_CALLS)",
    )
    parser.add_argument(
        "--max-cost", type=float, help="cap estimated cost below the cost sheet (env TRAILER_DIRECTOR_MAX_COST)"
    )
    parser.add_argument(
        "--no-fallback", action="store_true", help="fail instead of falling back when a provider is unavailable"
    )
    add_llm_arguments(parser)


def _load(args: argparse.Namespace) -> tuple[EpisodePackage, RepairLoop, PlannerFactory] | None:
    try:
        package = load_episode_package(args.data_dir)
        limits = run_limits(package.cost_sheet, args.max_model_calls, args.max_cost, not args.no_fallback)
        factory = PlannerFactory(package, args)
        repairer = factory.repairer(args.repair_mode, args.repair_replay_dir)
    except (DatasetLoadError, DatasetValidationError, PlannerError, ConfigError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return None
    loop = RepairLoop(
        package,
        ConstraintEngine(package),
        repairer,
        fallback_planner=ReplayPlanner(args.replay_dir),
        fallback_repairer=DeterministicRepairPlanner(package) if args.repair_mode != PlannerMode.MOCK else None,
        max_repair_attempts=args.max_attempts,
        max_model_calls=limits.max_model_calls,
        max_cost=limits.max_cost,
        allow_fallback=limits.allow_fallback,
    )
    return package, loop, factory


def _print(run: RepairRun, as_json: bool) -> None:
    if as_json:
        print(json.dumps(run.model_dump(mode="json", exclude_none=True), indent=2))
        return
    context = run.context
    print(
        f"Repair run {run.run_id} | audience={context.audience} date={context.evaluation_date} "
        f"territory={context.territory}"
    )
    print("Audit trail:")
    for entry in run.audit_trail:
        attempt = "-" if entry.attempt is None else f"#{entry.attempt}"
        print(f"  {attempt:<3} {entry.step:<17} {entry.detail}")
    budget = run.budget
    print(f"Result: {run.verdict} - {run.summary}")
    if run.fallbacks:
        print(f"Fallback used: {'; '.join(run.fallbacks)}")
    print(
        f"Budget: {budget.model_calls}/{budget.max_model_calls} model calls, "
        f"{budget.estimated_cost:.3f}/{budget.max_estimated_cost:.2f} {budget.currency}, "
        f"{budget.repair_attempts}/{budget.max_repair_attempts} repair attempts"
    )
    if budget.input_tokens or budget.output_tokens:
        print(f"Tokens: {budget.input_tokens} in, {budget.output_tokens} out (actual; cost above is the flat rate)")
    if run.final_proposal is None:
        return
    print(f'Final candidate: {run.final_proposal.candidate.trailer_id} | hook: "{run.final_proposal.hook}"')
    rationales = {r.clip_id: r for r in run.final_proposal.clip_rationales}
    for clip in run.final_proposal.candidate.clips:
        dump = clip.model_dump(mode="json")
        print(
            f"  {clip.clip_id} {clip.scene_id} {dump['source_in']}-{dump['source_out']} "
            f"lines {', '.join(clip.dialogue_ids) or '-'} music {clip.music_id or '-'}"
        )
        print(f"      why: {rationales[clip.clip_id].reason}")
