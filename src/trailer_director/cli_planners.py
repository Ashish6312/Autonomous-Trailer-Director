"""Builds the planner / repair planner a CLI command asked for (mock, replay or llm)."""

import argparse
from pathlib import Path
from typing import Any

from trailer_director.config import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    EFFORTS,
    ENV_EFFORT,
    ENV_MODEL,
    ConfigError,
    LLMSettings,
    llm_settings,
)
from trailer_director.domain import EpisodePackage
from trailer_director.errors import PlannerError
from trailer_director.planning import MockPlanner, Planner, PlannerMode, ReplayPlanner
from trailer_director.repair import DeterministicRepairPlanner, RepairPlanner, ReplayRepairPlanner


def add_llm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help=f"model for llm mode (default: ${ENV_MODEL} or {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort", choices=EFFORTS, help=f"effort for llm mode (default: ${ENV_EFFORT} or {DEFAULT_EFFORT})"
    )
    parser.add_argument("--record-dir", type=Path, help="save llm responses as replay fixtures under this directory")


class PlannerFactory:
    """Creates at most one API client per command, and only when llm mode is used."""

    def __init__(self, evidence: EpisodePackage, args: argparse.Namespace) -> None:
        self._evidence = evidence
        self._args = args
        self._client: Any = None

    def planner(self, mode: str, replay_dir: Path) -> Planner:
        if mode == PlannerMode.MOCK:
            return MockPlanner(self._evidence)
        if mode == PlannerMode.REPLAY:
            return ReplayPlanner(replay_dir)
        from trailer_director.llm import LLMPlanner, RecordingPlanner

        settings = self._settings()
        planner = LLMPlanner(self._evidence, self._llm_client(), settings.model, settings.effort)
        return RecordingPlanner(planner, self._args.record_dir) if self._args.record_dir else planner

    def repairer(self, mode: str, replay_dir: Path) -> RepairPlanner:
        if mode == PlannerMode.MOCK:
            return DeterministicRepairPlanner(self._evidence)
        if mode == PlannerMode.REPLAY:
            return ReplayRepairPlanner(replay_dir)
        from trailer_director.llm import LLMRepairPlanner, RecordingRepairPlanner

        settings = self._settings()
        repairer = LLMRepairPlanner(self._evidence, self._llm_client(), settings.model, settings.effort)
        return RecordingRepairPlanner(repairer, self._args.record_dir) if self._args.record_dir else repairer

    def _settings(self) -> LLMSettings:
        """Resolved only when llm mode is used, so a bad LLM setting never breaks mock or replay runs."""
        try:
            return llm_settings(self._args.model, self._args.effort)
        except ConfigError as exc:
            raise PlannerError(str(exc), retryable=False) from exc

    def _llm_client(self) -> Any:
        if self._client is None:
            from trailer_director.llm import create_client

            self._client = create_client(self._settings().timeout_seconds)
        return self._client
