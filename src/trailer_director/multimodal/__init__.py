"""Multimodal model gateway: capability routing to Gemini, mock or replay, with budget and recording."""

from trailer_director.multimodal.media import ModelInputError, input_records, request_fingerprint, sha256_of
from trailer_director.multimodal.providers import (
    GeminiProvider,
    MockProvider,
    ModelProvider,
    ModelUnavailableError,
    RecordingProvider,
    ReplayProvider,
)
from trailer_director.multimodal.router import ModelBudgetExceeded, ModelRouter
from trailer_director.multimodal.types import (
    Capability,
    InputPart,
    ModelObservation,
    ModelRequest,
    ModelResponse,
    observation_from,
)

__all__ = [
    "Capability",
    "GeminiProvider",
    "InputPart",
    "MockProvider",
    "ModelBudgetExceeded",
    "ModelInputError",
    "ModelObservation",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "ModelUnavailableError",
    "RecordingProvider",
    "ReplayProvider",
    "input_records",
    "observation_from",
    "request_fingerprint",
    "sha256_of",
]
