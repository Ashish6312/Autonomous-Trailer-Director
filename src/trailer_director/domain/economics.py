from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from trailer_director.domain.base import DomainModel, ensure_unique
from trailer_director.domain.enums import CostCategory
from trailer_director.domain.ids import NonEmptyStr


class UnitCost(DomainModel):
    item_id: NonEmptyStr
    category: CostCategory
    unit: NonEmptyStr
    unit_cost: float = Field(ge=0)
    description: NonEmptyStr


class BudgetLimits(DomainModel):
    max_model_calls: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_media_operations: int = Field(gt=0)
    max_estimated_total_cost: float = Field(gt=0)


class FallbackStrategy(DomainModel):
    on_budget_exhausted: Literal["return_best_verified_plan", "reject_with_report"]
    on_provider_unavailable: Literal["use_replay_fixtures", "reject_with_report"]
    max_provider_retries: int = Field(ge=0)
    retry_backoff_seconds: float = Field(ge=0)


class CostSheet(DomainModel):
    currency: NonEmptyStr
    effective_from: date
    notes: NonEmptyStr
    unit_costs: list[UnitCost] = Field(min_length=1)
    limits: BudgetLimits
    fallback_strategy: FallbackStrategy

    @model_validator(mode="after")
    def _check_items(self) -> Self:
        ensure_unique((item.item_id for item in self.unit_costs), "unit_costs.item_id")
        missing = sorted(set(CostCategory) - {item.category for item in self.unit_costs})
        if missing:
            raise ValueError(f"unit_costs missing categories: {', '.join(missing)}")
        return self

    def costs_in(self, category: CostCategory) -> list[UnitCost]:
        return [item for item in self.unit_costs if item.category is category]
