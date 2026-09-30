"""What each provider adapter implements, and whether it is configured. Built from the adapters themselves."""

from dataclasses import dataclass

from trailer_director.config import DEFAULT_MODEL, credential_present, modality_models
from trailer_director.multimodal.providers import GeminiProvider, MockProvider, ReplayProvider
from trailer_director.multimodal.types import Capability


@dataclass(frozen=True)
class ProviderInfo:
    provider: str
    role: str
    capabilities: frozenset[Capability]
    credentials: str
    models: dict[str, str]


def providers() -> list[ProviderInfo]:
    configured = modality_models()
    return [
        ProviderInfo(
            "gemini",
            "multimodal gateway (live)",
            GeminiProvider.capabilities,
            "GEMINI_API_KEY set" if credential_present("GEMINI_API_KEY") else "GEMINI_API_KEY not set",
            {c.value: configured.get(c.value, "not set") for c in Capability},
        ),
        ProviderInfo(
            "claude",
            "trailer planner and repairer (llm/planners.py); not routed through the gateway",
            frozenset({Capability.TEXT}),
            "ANTHROPIC_API_KEY set" if credential_present("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY not set",
            {Capability.TEXT.value: DEFAULT_MODEL},
        ),
        ProviderInfo(
            "mock", "deterministic test double", MockProvider.capabilities, "none", {"all": MockProvider.model}
        ),
        ProviderInfo(
            "replay", "recorded responses, no API call", ReplayProvider.capabilities, "none", {"all": "recorded"}
        ),
    ]
