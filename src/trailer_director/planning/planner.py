from typing import Protocol

from trailer_director.planning.models import PlannerMode, PlannerResponse
from trailer_director.planning.pool import EvidencePool
from trailer_director.planning.strategy import AudienceStrategy
from trailer_director.story import StoryMap


class Planner(Protocol):
    """Proposes a trailer. It never decides eligibility; its payload is untrusted until normalised.

    Raises ``PlannerError`` if it cannot produce any output at all.
    """

    mode: PlannerMode
    version: str

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse: ...
