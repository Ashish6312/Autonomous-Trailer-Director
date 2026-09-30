"""Episode-timeline timecodes.

Stored as integer milliseconds so comparisons and arithmetic are exact;
serialised back to ``HH:MM:SS.mmm`` so JSON stays human-readable.
"""

import re
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

_TIMECODE_RE = re.compile(r"^(\d{2}):([0-5]\d):([0-5]\d)\.(\d{3})$")
_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60 * _MS_PER_SECOND
_MS_PER_HOUR = 60 * _MS_PER_MINUTE


def parse_timecode(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError(f"timecode must be a string 'HH:MM:SS.mmm', got {type(value).__name__}")
    match = _TIMECODE_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid timecode '{value}', expected 'HH:MM:SS.mmm'")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * _MS_PER_HOUR + minutes * _MS_PER_MINUTE + seconds * _MS_PER_SECOND + millis


def format_timecode(total_ms: int) -> str:
    if total_ms < 0:
        raise ValueError(f"timecode cannot be negative: {total_ms} ms")
    hours, remainder = divmod(total_ms, _MS_PER_HOUR)
    minutes, remainder = divmod(remainder, _MS_PER_MINUTE)
    seconds, millis = divmod(remainder, _MS_PER_SECOND)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


Timecode = Annotated[int, BeforeValidator(parse_timecode), PlainSerializer(format_timecode, return_type=str)]
