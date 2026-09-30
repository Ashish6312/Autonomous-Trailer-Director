from pydantic import Field

from trailer_director.domain.base import DomainModel
from trailer_director.domain.enums import AudienceType
from trailer_director.domain.ids import CampaignId, NonEmptyStr, SceneId


class HistoricalPerformance(DomainModel):
    """One scene clip's result in a past test campaign.

    A candidate signal for planning, never a selection rule: a clip can
    perform well and still be ineligible (spoiler, rights, rating).
    """

    campaign_id: CampaignId
    scene_id: SceneId
    audience: AudienceType
    impressions: int = Field(gt=0)
    click_through_rate: float = Field(ge=0, le=1)
    completion_rate: float = Field(ge=0, le=1)
    engagement_score: float = Field(ge=0, le=100)
    notes: NonEmptyStr

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.campaign_id, self.scene_id, self.audience)
