"""Run budget, enforced from the cost sheet before every model call."""

from trailer_director.domain import CostSheet
from trailer_director.planning import TokenUsage
from trailer_director.repair.models import BudgetUsage

PLANNER_CALL_ITEM = "planner_model_call"
REPAIR_CALL_ITEM = "repair_model_call"


class BudgetLedger:
    def __init__(
        self,
        cost_sheet: CostSheet,
        max_repair_attempts: int,
        max_model_calls: int | None = None,
        max_cost: float | None = None,
    ) -> None:
        self._sheet = cost_sheet
        self._unit_costs = {item.item_id: item.unit_cost for item in cost_sheet.unit_costs}
        self.max_repair_attempts = max_repair_attempts
        limits = cost_sheet.limits
        self.max_model_calls = min(
            limits.max_model_calls, limits.max_model_calls if max_model_calls is None else max_model_calls
        )
        self.max_cost = min(
            limits.max_estimated_total_cost, limits.max_estimated_total_cost if max_cost is None else max_cost
        )
        self.model_calls = 0
        self.estimated_cost = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.repair_attempts = 0

    def cost_of(self, item_id: str, calls: int) -> float:
        return self._unit_costs[item_id] * calls

    def can_afford(self, item_id: str, calls: int) -> bool:
        return (
            self.model_calls + calls <= self.max_model_calls
            and self.estimated_cost + self.cost_of(item_id, calls) <= self.max_cost
        )

    def charge(self, item_id: str, calls: int, usage: TokenUsage | None = None) -> float:
        """Charge at the cost-sheet rate; actual token counts are recorded alongside for audit."""
        cost = self.cost_of(item_id, calls)
        self.model_calls += calls
        self.estimated_cost += cost
        if usage is not None:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
        return cost

    def usage(self) -> BudgetUsage:
        return BudgetUsage(
            model_calls=self.model_calls,
            max_model_calls=self.max_model_calls,
            estimated_cost=round(self.estimated_cost, 6),
            max_estimated_cost=self.max_cost,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            currency=self._sheet.currency,
            repair_attempts=self.repair_attempts,
            max_repair_attempts=self.max_repair_attempts,
        )
