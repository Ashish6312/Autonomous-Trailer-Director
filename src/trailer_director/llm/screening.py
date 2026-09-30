"""Detect evidence text that reads like an instruction to the model.

``llm.context`` withholds matching lines and scene summaries.
``find_instruction_like`` runs on the final context before a request is
sent; any match fails the call as non-retryable, so the fallback policy
applies. The patterns target attempts to override rules or roles, not
ordinary dialogue.
"""

import re
from typing import Any

_PATTERNS = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.!?\n]{0,60}?"
    r"\b(?:instructions?|rules?|restrictions?|contracts?|polic(?:y|ies)|guidelines?|constraints?|prompts?)\b"
    r"|\bsystem\s+prompt\b"
    r"|\byou\s+are\s+now\b"
    r"|\bdeveloper\s+mode\b"
    r"|\bjailbreak\b"
    r"|^\s*(?:system|assistant)\s*:",
    re.IGNORECASE | re.MULTILINE,
)


def is_instruction_like(text: str | None) -> bool:
    return bool(text) and _PATTERNS.search(text) is not None


def find_instruction_like(value: Any, path: str = "context") -> list[str]:
    """Paths of every string in a JSON-like value that reads like an instruction."""
    if isinstance(value, str):
        return [path] if is_instruction_like(value) else []
    if isinstance(value, dict):
        return [hit for key, item in value.items() for hit in find_instruction_like(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [hit for index, item in enumerate(value) for hit in find_instruction_like(item, f"{path}[{index}]")]
    return []
