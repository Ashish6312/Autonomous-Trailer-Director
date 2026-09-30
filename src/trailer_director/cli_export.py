"""CLI command ``export``: write the exported artifacts (maps, three trailers, validation report). Offline."""

import argparse
import sys
from pathlib import Path

from trailer_director.artifacts import write_artifacts
from trailer_director.data import load_episode_package
from trailer_director.errors import DatasetLoadError, DatasetValidationError
from trailer_director.evaluation import evaluate
from trailer_director.evaluation.scenarios import DEFAULT_FIXTURE_DIR

DEFAULT_OUT = Path("sample_run")
EXIT_OK = 0
EXIT_NOT_ALL_PASS = 1
EXIT_LOAD_ERROR = 2


def add_export_command(commands: argparse._SubParsersAction, dataset: argparse.ArgumentParser) -> None:
    parser = commands.add_parser(
        "export", parents=[dataset], help="write story/spoiler/constraint maps, three trailer plans and a report"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT.as_posix()})"
    )
    parser.add_argument(
        "--live-dir",
        type=Path,
        default=Path("runs"),
        help="recorded live responses to use where present (default: runs)",
    )
    parser.add_argument("--skip-evaluation", action="store_true", help="leave the evaluation summary out of the report")


def run_export(args: argparse.Namespace) -> int:
    try:
        package = load_episode_package(args.data_dir)
    except (DatasetLoadError, DatasetValidationError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_LOAD_ERROR
    live_dir = args.live_dir if args.live_dir.is_dir() else None
    report = None
    if not args.skip_evaluation:
        report = evaluate(package, fixture_dir=DEFAULT_FIXTURE_DIR, live_dir=live_dir).model_dump(mode="json")
    written = write_artifacts(args.out, package, live_dir, report)
    for path in written:
        print(f"wrote {path}")
    verdicts = [line for line in (args.out / "validation_report.md").read_text(encoding="utf-8").splitlines()]
    rejected = any("**REJECTED**" in line for line in verdicts)
    return EXIT_NOT_ALL_PASS if rejected else EXIT_OK
