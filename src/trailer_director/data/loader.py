import json
import logging
from pathlib import Path
from typing import Any

from trailer_director.data.validator import validate_dataset
from trailer_director.domain import EpisodePackage
from trailer_director.errors import DatasetLoadError, DatasetValidationError

logger = logging.getLogger(__name__)

DATASET_FILES: dict[str, Path] = {
    "episode": Path("episode/episode.json"),
    "characters": Path("episode/characters.json"),
    "locations": Path("episode/locations.json"),
    "props": Path("episode/props.json"),
    "scenes": Path("episode/scenes.json"),
    "dialogue": Path("episode/dialogue.json"),
    "actor_rights": Path("rights/actors.json"),
    "music": Path("rights/music.json"),
    "rating_policies": Path("policies/rating_policies.json"),
    "audience_profiles": Path("audiences/profiles.json"),
    "historical_performance": Path("performance/historical_campaigns.json"),
    "cost_sheet": Path("economics/cost_sheet.json"),
}


def load_raw_dataset(data_dir: Path) -> dict[str, Any]:
    """Read every dataset file as JSON without interpreting it."""
    if not data_dir.is_dir():
        raise DatasetLoadError(f"data directory not found: {data_dir}")
    return {name: _read_json(data_dir / relative_path) for name, relative_path in DATASET_FILES.items()}


def load_episode_package(data_dir: Path) -> EpisodePackage:
    """Load and validate the dataset, raising if any error is found."""
    package, report = validate_dataset(load_raw_dataset(data_dir))
    for warning in report.warnings:
        logger.warning("%s", warning)
    if package is None:
        raise DatasetValidationError(report)
    logger.info("loaded episode %s from %s", package.episode.episode_id, data_dir)
    return package


def _read_json(path: Path) -> Any:
    logger.debug("reading %s", path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DatasetLoadError(f"missing dataset file: {path}") from None
    except UnicodeDecodeError as exc:
        raise DatasetLoadError(f"{path}: not valid UTF-8 ({exc.reason})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetLoadError(f"{path}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc
