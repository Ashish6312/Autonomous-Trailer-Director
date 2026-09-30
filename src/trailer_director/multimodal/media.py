"""Input checks and content fingerprints. Media is identified by SHA-256; its bytes never enter a record."""

import hashlib
import json
from pathlib import Path

from trailer_director.errors import TrailerDirectorError
from trailer_director.multimodal.types import InputPart, InputRecord, ModelRequest

MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}
_FAMILY = {"image": "image/", "audio": "audio/", "video": "video/"}


class ModelInputError(TrailerDirectorError):
    """An input is missing, unreadable or of the wrong kind; raised before any call is made."""


def input_records(request: ModelRequest) -> list[InputRecord]:
    return [_record(part) for part in request.inputs]


def request_fingerprint(request: ModelRequest, records: list[InputRecord]) -> str:
    """Same capability, model override, text and media content -> same fingerprint (the replay key)."""
    content = {
        "capability": request.capability,
        "model": request.model,
        "inputs": [{"type": r.type, "text": r.text, "sha256": r.sha256} for r in records],
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _record(part: InputPart) -> InputRecord:
    if part.type == "text":
        return InputRecord(type="text", text=part.content)
    path = part.path
    if not path.is_file():
        raise ModelInputError(f"{part.type} input not found: {path}")
    mime = MIME_TYPES.get(path.suffix.lower())
    if mime is None or not mime.startswith(_FAMILY[part.type]):
        raise ModelInputError(f"{path.name} is not a supported {part.type} file")
    try:
        sha = sha256_of(path)
    except OSError as exc:
        raise ModelInputError(f"cannot read {path}: {exc}") from exc
    return InputRecord(type=part.type, file_name=path.name, mime_type=mime, size_bytes=path.stat().st_size, sha256=sha)
