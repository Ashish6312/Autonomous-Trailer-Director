import copy
from typing import Any

import pytest
from support import DATA_DIR

from trailer_director.constraints import ConstraintEngine
from trailer_director.data import load_episode_package, load_raw_dataset
from trailer_director.domain import EpisodePackage
from trailer_director.story import StoryMap, build_story_map


@pytest.fixture(scope="session")
def evidence() -> EpisodePackage:
    return load_episode_package(DATA_DIR)


@pytest.fixture(scope="session")
def engine(evidence: EpisodePackage) -> ConstraintEngine:
    return ConstraintEngine(evidence)


@pytest.fixture(scope="session")
def story_map(evidence: EpisodePackage) -> StoryMap:
    return build_story_map(evidence)


@pytest.fixture(scope="session")
def pristine_raw() -> dict[str, Any]:
    return load_raw_dataset(DATA_DIR)


@pytest.fixture
def raw(pristine_raw: dict[str, Any]) -> dict[str, Any]:
    """A private copy of the shipped dataset that a test may mutate."""
    return copy.deepcopy(pristine_raw)
