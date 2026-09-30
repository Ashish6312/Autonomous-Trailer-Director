"""Provider-independent request, response and evidence types for the model gateway."""

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from trailer_director.domain.base import DomainModel
from trailer_director.planning import TokenUsage


class Capability(StrEnum):
    TEXT = "text"
    VISION = "vision"
    AUDIO = "audio"
    VIDEO = "video"


InputType = Literal["text", "image", "audio", "video"]

# The media input each capability analyses; TEXT takes text only.
MEDIA_TYPE: dict[Capability, InputType] = {
    Capability.VISION: "image",
    Capability.AUDIO: "audio",
    Capability.VIDEO: "video",
}


class InputPart(DomainModel):
    type: InputType
    content: str | None = None
    path: Path | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if self.type == "text" and not self.content:
            raise ValueError("a text input needs content")
        if self.type != "text" and self.path is None:
            raise ValueError(f"an {self.type} input needs a path")
        return self


class ModelRequest(DomainModel):
    capability: Capability
    inputs: list[InputPart]
    model: str | None = None
    """Overrides the configured model for this capability."""

    @model_validator(mode="after")
    def _inputs_match_capability(self) -> Self:
        expected = MEDIA_TYPE.get(self.capability)
        media = [part.type for part in self.inputs if part.type != "text"]
        if expected is None and media:
            raise ValueError("a text request cannot carry media")
        if expected is not None and (not media or any(t != expected for t in media)):
            raise ValueError(f"a {self.capability} request needs {expected} input(s) and no other media")
        return self


class InputRecord(DomainModel):
    """What a recording keeps of one input: text as sent, media by name, type, size and SHA-256 only."""

    type: InputType
    text: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class ModelResponse(DomainModel):
    provider: str
    model: str
    capability: Capability
    text: str
    request_fingerprint: str
    inputs: list[InputRecord]
    usage: TokenUsage | None = None
    latency_seconds: float | None = None
    model_calls: int = 1
    estimated_cost: float | None = None
    replayed: bool = False


class ModelObservation(DomainModel):
    """A model's description of an input. Evidence with uncertainty, never story truth."""

    evidence_id: str
    modality: InputType
    source_sha256: str | None
    provider: str
    model: str
    observation: str
    status: Literal["model_observation", "mock_output"] = "model_observation"
    authoritative: Literal[False] = False


def observation_from(response: ModelResponse) -> ModelObservation:
    media = next((i for i in response.inputs if i.type != "text"), None)
    return ModelObservation(
        evidence_id=f"OBS_{response.request_fingerprint[:12]}",
        modality=media.type if media else "text",
        source_sha256=media.sha256 if media else None,
        provider=response.provider,
        model=response.model,
        observation=response.text,
        status="mock_output" if response.provider == "mock" else "model_observation",
    )
