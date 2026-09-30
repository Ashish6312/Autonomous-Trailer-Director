"""Runtime settings: CLI flag, then environment variable, then default.

Run limits can only tighten the cost sheet. Credentials are not handled here;
the Anthropic SDK resolves them (``ANTHROPIC_API_KEY`` or ``ant auth login``).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from trailer_director.domain import CostSheet
from trailer_director.errors import TrailerDirectorError

ENV_MODEL = "TRAILER_DIRECTOR_MODEL"
ENV_EFFORT = "TRAILER_DIRECTOR_EFFORT"
ENV_TIMEOUT = "TRAILER_DIRECTOR_LLM_TIMEOUT_SECONDS"
ENV_MAX_MODEL_CALLS = "TRAILER_DIRECTOR_MAX_MODEL_CALLS"
ENV_MAX_COST = "TRAILER_DIRECTOR_MAX_COST"

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT_SECONDS = 300.0
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(TrailerDirectorError):
    """A setting is present but invalid."""


@dataclass(frozen=True)
class LLMSettings:
    model: str
    effort: str
    timeout_seconds: float


def llm_settings(
    model: str | None = None,
    effort: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMSettings:
    """Explicit values win over the environment, which wins over the defaults."""
    env = os.environ if environ is None else environ
    model = model or env.get(ENV_MODEL, "").strip() or DEFAULT_MODEL
    effort = effort or env.get(ENV_EFFORT, "").strip() or DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise ConfigError(f"{ENV_EFFORT}={effort!r} is not one of {', '.join(EFFORTS)}")
    raw_timeout = env.get(ENV_TIMEOUT, "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        raise ConfigError(f"{ENV_TIMEOUT}={raw_timeout!r} is not a number of seconds") from None
    if timeout <= 0:
        raise ConfigError(f"{ENV_TIMEOUT} must be positive, got {timeout:g}")
    return LLMSettings(model=model, effort=effort, timeout_seconds=timeout)


@dataclass(frozen=True)
class RunLimits:
    """Per-run tightening of the cost sheet: a run may spend less than the sheet allows, never more."""

    max_model_calls: int
    max_cost: float
    allow_fallback: bool = True


def run_limits(
    sheet: CostSheet,
    max_model_calls: int | None = None,
    max_cost: float | None = None,
    allow_fallback: bool = True,
    environ: Mapping[str, str] | None = None,
) -> RunLimits:
    env = os.environ if environ is None else environ
    calls = max_model_calls if max_model_calls is not None else _number(env, ENV_MAX_MODEL_CALLS, int)
    cost = max_cost if max_cost is not None else _number(env, ENV_MAX_COST, float)
    limits = sheet.limits
    if calls is not None and not 0 <= calls <= limits.max_model_calls:
        raise ConfigError(
            f"max model calls must be between 0 and the cost sheet's {limits.max_model_calls}, got {calls}"
        )
    if cost is not None and not 0 <= cost <= limits.max_estimated_total_cost:
        raise ConfigError(
            f"max cost must be between 0 and the cost sheet's {limits.max_estimated_total_cost:g}, got {cost:g}"
        )
    return RunLimits(
        max_model_calls=limits.max_model_calls if calls is None else calls,
        max_cost=limits.max_estimated_total_cost if cost is None else cost,
        allow_fallback=allow_fallback,
    )


def _number(env: Mapping[str, str], name: str, kind: type) -> Any:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        return kind(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid {kind.__name__}") from None


MODALITY_MODEL_ENV = {
    "text": "TRAILER_TEXT_MODEL",
    "vision": "TRAILER_VISION_MODEL",
    "audio": "TRAILER_AUDIO_MODEL",
    "video": "TRAILER_VIDEO_MODEL",
}
"""Model per capability for the multimodal gateway. No defaults: an unset capability is reported, not guessed."""


def modality_models(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    return {
        capability: env[name].strip() for capability, name in MODALITY_MODEL_ENV.items() if env.get(name, "").strip()
    }


def credential_present(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether a credential variable is set. Only the fact is reported, never the value."""
    env = os.environ if environ is None else environ
    return bool(env.get(name, "").strip())
