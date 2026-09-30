"""CLI command for the evaluation: ``evaluate``. Offline only: no scenario makes an API call."""

import argparse
import sys
from pathlib import Path

from trailer_director.data import load_episode_package
from trailer_director.domain import AudienceType
from trailer_director.errors import DatasetLoadError, DatasetValidationError
from trailer_director.evaluation import SCENARIOS, EvaluationReport, collect_plans, evaluate, render_review_sheet
from trailer_director.evaluation.harness import LIVE_SCENARIO_ID
from trailer_director.evaluation.scenarios import DEFAULT_FIXTURE_DIR

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_LOAD_ERROR = 2
DEFAULT_LIVE_DIR = Path("runs")


def add_evaluate_command(commands: argparse._SubParsersAction, dataset: argparse.ArgumentParser) -> None:
    parser = commands.add_parser(
        "evaluate", parents=[dataset], help="run the evaluation scenarios offline and report factual metrics per run"
    )
    parser.add_argument(
        "--audience", action="append", choices=[a.value for a in AudienceType], help="repeatable (default: all)"
    )
    scenario_ids = [s.scenario_id for s in SCENARIOS] + [LIVE_SCENARIO_ID]
    parser.add_argument("--scenario", action="append", choices=scenario_ids, help="repeatable (default: all)")
    parser.add_argument(
        "--live-dir",
        type=Path,
        default=DEFAULT_LIVE_DIR,
        help=f"recorded live responses to replay as {LIVE_SCENARIO_ID} (default: {DEFAULT_LIVE_DIR})",
    )
    parser.add_argument("--json", action="store_true", help="print the evaluation report as JSON")
    parser.add_argument("--out", type=Path, help="also write the JSON report to this file")
    parser.add_argument("--review-sheet", type=Path, help="write the human creative review sheet (Markdown) here")


def run_evaluate(args: argparse.Namespace) -> int:
    try:
        package = load_episode_package(args.data_dir)
    except (DatasetLoadError, DatasetValidationError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR
    audiences = [AudienceType(a) for a in args.audience] if args.audience else None
    live_dir = args.live_dir if args.live_dir.is_dir() else None
    report = evaluate(package, audiences, args.scenario, DEFAULT_FIXTURE_DIR, live_dir)

    as_json = report.model_dump_json(indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(as_json + "\n", encoding="utf-8", newline="\n")
    if args.review_sheet:
        args.review_sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet = render_review_sheet(package, collect_plans(package, live_dir))
        args.review_sheet.write_text(sheet, encoding="utf-8", newline="\n")
    print(as_json if args.json else _table(report))
    return EXIT_OK if not report.unexpected else EXIT_UNEXPECTED


def _table(report: EvaluationReport) -> str:
    header = (
        f"{'scenario':<26} {'audience':<15} {'initial':<15} {'final':<18} {'viol':>4} {'rep':>3} "
        f"{'p/r calls':>9} {'fb':>3} {'cost':>6} {'replay':>6}  expected"
    )
    lines = [
        f"Evaluation | {report.episode_id} evidence {report.evidence_fingerprint} | "
        f"{report.scenario_count} scenarios, {report.run_count} runs (each run twice)",
        header,
        "-" * len(header),
    ]
    for r in report.results:
        lines.append(
            f"{r.scenario_id:<26} {r.audience:<15} {r.initial_status:<15} {r.final_status:<18} "
            f"{r.violation_count:>4} {r.repair_attempts:>3} {f'{r.planner_calls}/{r.repair_calls}':>9} "
            f"{'yes' if r.fallback_used else '-':>3} {r.estimated_cost:>6.3f} "
            f"{'match' if r.replay_match else 'DIFF':>6}  "
            f"{'yes' if r.as_expected else 'NO: ' + '; '.join(r.deviations)}"
        )
    for entry in report.not_applicable:
        lines.append(f"not applicable: {entry}")
    lines.append(
        "All runs behaved as designed." if not report.unexpected else f"Unexpected: {', '.join(report.unexpected)}"
    )
    lines.append("Metrics are factual per run; there is no aggregate score. Creative quality is human-reviewed.")
    return "\n".join(lines)
