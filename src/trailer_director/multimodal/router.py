"""Capability-based routing with the run budget and an explicit, recorded fallback.

Callers ask for a capability; the router picks the provider configured for
it, validates the inputs, checks the budget before the call, and charges it
after. There are no silent retries or fallbacks: an unavailable provider
either fails with ``ModelUnavailableError`` or, only when a fallback was
explicitly configured, switches to it and records the switch.
"""

from trailer_director.multimodal.media import input_records, request_fingerprint
from trailer_director.multimodal.providers import ModelProvider, ModelUnavailableError
from trailer_director.multimodal.types import Capability, ModelRequest, ModelResponse
from trailer_director.repair.budget import BudgetLedger

MODEL_CALL_ITEM = "verifier_model_call"
"""Cost-sheet rate charged per multimodal call; the sheet has no dedicated multimodal item."""


class ModelBudgetExceeded(ModelUnavailableError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class ModelRouter:
    def __init__(
        self,
        providers: dict[Capability, ModelProvider],
        ledger: BudgetLedger,
        fallback: ModelProvider | None = None,
    ) -> None:
        self._providers = providers
        self._ledger = ledger
        self._fallback = fallback
        self.fallbacks: list[str] = []
        self.responses: list[ModelResponse] = []

    def run(self, request: ModelRequest) -> ModelResponse:
        provider = self._providers.get(request.capability)
        if provider is None or request.capability not in provider.capabilities:
            raise ModelUnavailableError(f"no provider configured for {request.capability}", retryable=False)
        records = input_records(request)
        fingerprint = request_fingerprint(request, records)
        try:
            response = self._call(provider, request, records, fingerprint)
        except ModelBudgetExceeded:
            raise
        except ModelUnavailableError as exc:
            if self._fallback is None or request.capability not in self._fallback.capabilities:
                raise
            self.fallbacks.append(f"{request.capability}: {provider.name} unavailable ({exc}) -> {self._fallback.name}")
            response = self._call(self._fallback, request, records, fingerprint)
        self.responses.append(response)
        return response

    def _call(self, provider: ModelProvider, request, records, fingerprint: str) -> ModelResponse:
        provider.model_for(request)  # fails before any spend if no model is configured
        expected = 0 if provider.name == "mock" else 1
        if not self._ledger.can_afford(MODEL_CALL_ITEM, expected):
            usage = self._ledger.usage()
            raise ModelBudgetExceeded(
                f"model budget exhausted ({usage.model_calls}/{usage.max_model_calls} calls, "
                f"{usage.estimated_cost:.3f}/{usage.max_estimated_cost:g} {usage.currency}); call not made"
            )
        response = provider.generate(request, records, fingerprint)
        cost = self._ledger.charge(MODEL_CALL_ITEM, response.model_calls, response.usage)
        return response.model_copy(update={"estimated_cost": cost})
