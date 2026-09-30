"""Claude-backed planner and repairer behind the ``Planner`` / ``RepairPlanner`` interfaces.

The model gets pre-filtered evidence (``llm.context``) and returns JSON
constrained by ``PLAN_SCHEMA``; the output is untrusted and verified like any
other planner's. API failures, refusals, timeouts and truncation become
``PlannerError`` so the loop's retry and fallback policy applies. SDK retries
are off, so every call goes through the budget check.
"""

import json
import time
from typing import Any

from trailer_director.config import DEFAULT_EFFORT, DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS
from trailer_director.domain import EpisodePackage, ValidationSeverity
from trailer_director.errors import PlannerError
from trailer_director.llm.context import planning_context, repair_context
from trailer_director.llm.prompts import PLAN_SYSTEM, REPAIR_SYSTEM
from trailer_director.llm.schema import PLAN_SCHEMA
from trailer_director.llm.screening import find_instruction_like
from trailer_director.planning import (
    AudienceStrategy,
    EvidencePool,
    IssueCode,
    PlannerMode,
    PlannerResponse,
    PlanningIssue,
    TokenUsage,
)
from trailer_director.repair import RepairRequest
from trailer_director.story import StoryMap

MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def create_client(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    """Anthropic client with SDK retries off (the repair loop owns the retry policy) and a bounded timeout."""
    try:
        import anthropic
    except ImportError as exc:
        raise PlannerError('LLM mode needs the Anthropic SDK: pip install -e ".[llm]"', retryable=False) from exc
    try:
        return anthropic.Anthropic(max_retries=0, timeout=timeout_seconds)
    except anthropic.AnthropicError as exc:
        raise PlannerError(f"cannot create the Anthropic client: {exc}", retryable=False) from exc


class _ClaudeCaller:
    def __init__(self, client: Any, model: str, effort: str) -> None:
        self._client = client
        self.model = model
        self.effort = effort

    def call(self, system: str, context: dict[str, Any]) -> tuple[Any, TokenUsage, float]:
        flagged = find_instruction_like(context)
        if flagged:
            # Fail closed: the context filter should have withheld these; never send them.
            raise PlannerError(
                f"model context holds instruction-like text at {', '.join(flagged)}; request not sent",
                retryable=False,
            )
        started = time.perf_counter()
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=1)}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            raise _as_planner_error(exc) from exc
        latency = round(time.perf_counter() - started, 3)

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise PlannerError(f"model declined the request (refusal, category {category})", retryable=False)
        if response.stop_reason == "max_tokens":
            raise PlannerError(f"model output was cut off at max_tokens={MAX_TOKENS}", retryable=False)
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise PlannerError(f"model returned no text output (stop_reason {response.stop_reason})")
        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=getattr(response, "model", None),
        )
        try:
            return json.loads(text), usage, latency
        except json.JSONDecodeError:
            # Passed on as-is: the normaliser reports it as malformed output, and the loop can ask for a fresh plan.
            return text, usage, latency


class LLMPlanner:
    mode = PlannerMode.LLM

    def __init__(
        self, evidence: EpisodePackage, client: Any, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT
    ) -> None:
        self._evidence = evidence
        self._caller = _ClaudeCaller(client, model, effort)
        self.version = f"llm-planner/{model}/{effort}"

    def plan(self, story_map: StoryMap, strategy: AudienceStrategy, pool: EvidencePool) -> PlannerResponse:
        context = planning_context(story_map, strategy, pool, self._evidence)
        payload, usage, latency = self._caller.call(PLAN_SYSTEM, context)
        return PlannerResponse(
            mode=self.mode,
            planner_version=self.version,
            payload=payload,
            model_calls=1,
            usage=usage,
            latency_seconds=latency,
            issues=_withheld_issues(context),
        )


class LLMRepairPlanner:
    mode = PlannerMode.LLM

    def __init__(
        self, evidence: EpisodePackage, client: Any, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT
    ) -> None:
        self._evidence = evidence
        self._caller = _ClaudeCaller(client, model, effort)
        self.version = f"llm-repair/{model}/{effort}"

    def repair(self, request: RepairRequest) -> PlannerResponse:
        context = repair_context(request, self._evidence)
        payload, usage, latency = self._caller.call(REPAIR_SYSTEM, context)
        return PlannerResponse(
            mode=self.mode,
            planner_version=self.version,
            payload=payload,
            model_calls=1,
            usage=usage,
            latency_seconds=latency,
            issues=_withheld_issues(context),
        )


def _withheld_issues(context: dict[str, Any]) -> list[PlanningIssue]:
    """Record, for the audit trail, which evidence text was kept from the model and why."""
    return [
        PlanningIssue(
            severity=ValidationSeverity.WARNING,
            code=IssueCode.EVIDENCE_WITHHELD,
            location=f"llm_context.{entity_id}",
            message="text reads like an instruction; withheld from the model",
        )
        for entity_id in context["withheld_text"]
    ]


def _as_planner_error(exc: Exception) -> PlannerError:
    """Map SDK failures to PlannerError with enough detail to act on; anything else is a bug and re-raised."""
    import anthropic

    if isinstance(exc, anthropic.RateLimitError):
        return PlannerError("model provider rate limit (429)")
    if isinstance(exc, anthropic.APIStatusError):
        retryable = exc.status_code >= 500 or exc.status_code in (408, 409)
        return PlannerError(f"model provider error {exc.status_code}: {exc.message}", retryable=retryable)
    if isinstance(exc, anthropic.APITimeoutError):
        return PlannerError("model provider timed out")
    if isinstance(exc, anthropic.APIConnectionError):
        return PlannerError(f"model provider unreachable: {exc}")
    if isinstance(exc, TypeError) and "authentication" in str(exc):
        # The SDK resolves credentials lazily and raises TypeError at request time when none are found.
        return PlannerError(
            "no Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login` (see docs/REFERENCE.md)",
            retryable=False,
        )
    raise exc
