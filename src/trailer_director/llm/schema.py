"""JSON schema for model output: the same payload shape every planner hands to the normaliser.

Structured outputs make the model return JSON of this shape. That only
guarantees shape - IDs, timecodes and eligibility are still checked by the
normaliser, the decision guard and the constraint engine.
"""

from typing import Any

from trailer_director.domain import AudienceType

_STRING = {"type": "string"}

_CLIP: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clip_id": {"type": "string", "description": "CLIP_001, CLIP_002, ... Keep existing IDs when repairing."},
        "scene_id": {"type": "string", "description": "A scene ID from the eligible evidence."},
        "source_in": {"type": "string", "description": "HH:MM:SS.mmm, inside the scene."},
        "source_out": {"type": "string", "description": "HH:MM:SS.mmm, after source_in, inside the scene."},
        "dialogue_ids": {"type": "array", "items": _STRING},
        "music_id": {"anyOf": [_STRING, {"type": "null"}]},
        "purpose": {"type": "string", "description": "The clip's creative job in the trailer."},
        "reason": {"type": "string", "description": "Why this clip, citing the evidence IDs it relies on."},
        "evidence": {"type": "array", "items": _STRING, "description": "Evidence IDs the reason relies on."},
    },
    "required": [
        "clip_id",
        "scene_id",
        "source_in",
        "source_out",
        "dialogue_ids",
        "music_id",
        "purpose",
        "reason",
        "evidence",
    ],
    "additionalProperties": False,
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "audience": {"type": "string", "enum": [audience.value for audience in AudienceType]},
        "trailer_id": {"type": "string", "description": "TRL_ followed by capitals, digits or underscores."},
        "title": _STRING,
        "hook": _STRING,
        "positioning": _STRING,
        "rationale": _STRING,
        "clips": {"type": "array", "items": _CLIP},
    },
    "required": ["audience", "trailer_id", "title", "hook", "positioning", "rationale", "clips"],
    "additionalProperties": False,
}
