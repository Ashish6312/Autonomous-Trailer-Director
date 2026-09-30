"""Offline stand-in for the Anthropic client: scripted replies, recorded requests, no network."""

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    def __init__(self, replies: list[Any]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply


class FakeClient:
    """Mimics ``client.beta.messages.create``; replays the last reply once the script runs out."""

    def __init__(self, *replies: Any) -> None:
        self.messages = FakeMessages(list(replies))
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.messages.calls

    def context_of(self, call_index: int) -> dict[str, Any]:
        return json.loads(self.calls[call_index]["messages"][0]["content"])


def reply(
    payload: Any,
    stop_reason: str = "end_turn",
    input_tokens: int = 1200,
    output_tokens: int = 300,
    refusal_category: str | None = None,
) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=refusal_category) if stop_reason == "refusal" else None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def status_error(status: int) -> anthropic.APIStatusError:
    classes = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        429: anthropic.RateLimitError,
        529: anthropic.InternalServerError,
    }
    return classes[status](f"status {status}", response=httpx2.Response(status, request=_REQUEST), body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(message="connection reset", request=_REQUEST)
