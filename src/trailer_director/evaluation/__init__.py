"""Evaluation: scenarios through the unchanged pipeline, factual metrics, and a human creative review sheet."""

from trailer_director.evaluation.harness import EvaluationReport, ScenarioResult, evaluate
from trailer_director.evaluation.review import collect_plans, render_review_sheet
from trailer_director.evaluation.scenarios import SCENARIOS, Scenario

__all__ = [
    "SCENARIOS",
    "EvaluationReport",
    "Scenario",
    "ScenarioResult",
    "collect_plans",
    "evaluate",
    "render_review_sheet",
]
