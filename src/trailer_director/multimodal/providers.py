"""Model providers behind one interface: Gemini (live), mock (tests), and record / replay wrappers."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from trailer_director.domain.base import DomainModel
from trailer_director.errors import TrailerDirectorError
from trailer_director.multimodal.media import MIME_TYPES, ModelInputError
from trailer_director.multimodal.types import Capability, InputRecord, ModelRequest, ModelResponse
from trailer_director.planning import TokenUsage

MAX_INLINE_BYTES = 20 * 1024 * 1024
"""Gemini's inline request limit. Larger media needs the Files API, which is not implemented."""


class ModelUnavailableError(TrailerDirectorError):
    """The provider could not answer. ``retryable`` is False when trying again cannot help."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelProvider(Protocol):
    name: str
    capabilities: frozenset[Capability]

    def model_for(self, request: ModelRequest) -> str: ...

    def generate(self, request: ModelRequest, records: list[InputRecord], fingerprint: str) -> ModelResponse: ...


class MockProvider:
    """Deterministic stand-in for tests. Its output says it is mock output and is never evidence."""

    name = "mock"
    capabilities = frozenset(Capability)
    model = "mock-multimodal-1"

    def model_for(self, request: ModelRequest) -> str:
        return request.model or self.model

    def generate(self, request: ModelRequest, records: list[InputRecord], fingerprint: str) -> ModelResponse:
        media = [r for r in records if r.type != "text"]
        subject = ", ".join(f"{r.type} {r.sha256[:12]}" for r in media) or "text only"
        return ModelResponse(
            provider=self.name,
            model=self.model_for(request),
            capability=request.capability,
            text=f"[mock {request.capability} output for {subject}; not a model observation]",
            request_fingerprint=fingerprint,
            inputs=records,
            model_calls=0,
        )


class GeminiProvider:
    """Google Gemini through the official ``google-genai`` SDK.

    The SDK reads ``GEMINI_API_KEY`` itself; this class never sees the key. SDK
    retries are off so every call goes through the gateway's budget check.
    """

    name = "gemini"
    capabilities = frozenset(Capability)

    def __init__(self, models: dict[Capability, str], timeout_seconds: float, client: Any = None) -> None:
        self._models = models
        self._timeout_ms = int(timeout_seconds * 1000)
        self._client = client

    def model_for(self, request: ModelRequest) -> str:
        model = request.model or self._models.get(request.capability)
        if not model:
            raise ModelUnavailableError(
                f"no Gemini model configured for {request.capability} (set TRAILER_{request.capability.upper()}_MODEL)",
                retryable=False,
            )
        return model

    def generate(self, request: ModelRequest, records: list[InputRecord], fingerprint: str) -> ModelResponse:
        model = self.model_for(request)
        from google.genai import types

        contents = [self._part(part) for part in request.inputs]
        client = self._get_client()
        # No tools are passed, so the SDK's automatic function calling has nothing to do.
        config = types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        started = time.perf_counter()
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            raise _as_unavailable(exc) from exc
        latency = round(time.perf_counter() - started, 3)
        usage = getattr(response, "usage_metadata", None)
        served = getattr(response, "model_version", None) or model
        return ModelResponse(
            provider=self.name,
            model=served,
            capability=request.capability,
            text=(response.text or "").strip(),
            request_fingerprint=fingerprint,
            inputs=records,
            usage=TokenUsage(
                input_tokens=getattr(usage, "prompt_token_count", None) or 0,
                output_tokens=getattr(usage, "candidates_token_count", None) or 0,
                model=served,
            )
            if usage
            else None,
            latency_seconds=latency,
        )

    def _part(self, part) -> Any:
        if part.type == "text":
            return part.content
        from google.genai import types

        data = part.path.read_bytes()
        if len(data) > MAX_INLINE_BYTES:
            raise ModelInputError(f"{part.path.name} is over the 20 MB inline limit; the Files API is not implemented")
        return types.Part.from_bytes(data=data, mime_type=MIME_TYPES[part.path.suffix.lower()])

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise ModelUnavailableError(
                    'Gemini needs the Google GenAI SDK: pip install -e ".[multimodal]"', retryable=False
                ) from exc
            options = types.HttpOptions(timeout=self._timeout_ms, retry_options=types.HttpRetryOptions(attempts=1))
            try:
                self._client = genai.Client(http_options=options)
            except ValueError as exc:
                raise ModelUnavailableError("no Gemini credentials: set GEMINI_API_KEY", retryable=False) from exc
        return self._client


def _as_unavailable(exc: Exception) -> Exception:
    """Map SDK failures to ModelUnavailableError; anything else is a bug and propagates unchanged."""
    import httpx
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        retryable = exc.code == 429 or exc.code >= 500 or exc.code in (408, 409)
        return ModelUnavailableError(f"Gemini error {exc.code}: {exc.message}", retryable=retryable)
    if isinstance(exc, httpx.TimeoutException):
        return ModelUnavailableError("Gemini request timed out")
    if isinstance(exc, httpx.TransportError):
        return ModelUnavailableError(f"Gemini unreachable: {exc}")
    return exc


class ModelCallRecord(DomainModel):
    format_version: Literal[1]
    recorded_at: datetime
    response: ModelResponse


class RecordingProvider:
    """Saves every response as ``<dir>/<request fingerprint>.json`` so it can be replayed offline."""

    def __init__(
        self, inner: ModelProvider, out_dir: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._clock = clock
        self.name = inner.name
        self.capabilities = inner.capabilities

    def model_for(self, request: ModelRequest) -> str:
        return self._inner.model_for(request)

    def generate(self, request: ModelRequest, records: list[InputRecord], fingerprint: str) -> ModelResponse:
        response = self._inner.generate(request, records, fingerprint)
        record = ModelCallRecord(format_version=1, recorded_at=self._clock(), response=response)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self._out_dir / f"{fingerprint}.json"
        path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        return response


class ReplayProvider:
    """Returns the recorded response for the same request fingerprint; makes no API call."""

    name = "replay"
    capabilities = frozenset(Capability)

    def __init__(self, replay_dir: Path) -> None:
        self._dir = replay_dir

    def model_for(self, request: ModelRequest) -> str:
        return request.model or "recorded"

    def generate(self, request: ModelRequest, records: list[InputRecord], fingerprint: str) -> ModelResponse:
        path = self._dir / f"{fingerprint}.json"
        if not path.is_file():
            raise ModelUnavailableError(f"no recorded response for this request in {self._dir}", retryable=False)
        recorded = ModelCallRecord.model_validate_json(path.read_text(encoding="utf-8")).response
        return recorded.model_copy(update={"latency_seconds": None, "replayed": True})
