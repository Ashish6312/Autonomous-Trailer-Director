"""Minimal edit decision list: what a planner proposes, before anything checks it.

These models validate shape only (ID formats, timecode format, in < out).
Whether the referenced scene, lines and music exist and may be used is
decided by the constraint engine against the evidence package.
"""

from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from trailer_director.domain.base import DomainModel, ensure_unique
from trailer_director.domain.ids import DialogueId, MusicId, NonEmptyStr, SceneId
from trailer_director.domain.timecode import Timecode

ClipId = Annotated[str, StringConstraints(pattern=r"^CLIP_[A-Z0-9_]+$")]
TrailerId = Annotated[str, StringConstraints(pattern=r"^TRL_[A-Z0-9_]+$")]


class TrailerClip(DomainModel):
    clip_id: ClipId
    scene_id: SceneId
    source_in: Timecode
    source_out: Timecode
    dialogue_ids: list[DialogueId] = []
    music_id: MusicId | None = None
    purpose: NonEmptyStr

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.source_out <= self.source_in:
            raise ValueError("source_out must be after source_in")
        ensure_unique(self.dialogue_ids, "dialogue_ids")
        return self

    @property
    def duration_ms(self) -> int:
        return self.source_out - self.source_in


class TrailerCandidate(DomainModel):
    trailer_id: TrailerId
    clips: list[TrailerClip] = Field(min_length=1)

    @model_validator(mode="after")
    def _clip_ids_unique(self) -> Self:
        ensure_unique((clip.clip_id for clip in self.clips), "clips.clip_id")
        return self

    @property
    def duration_ms(self) -> int:
        return sum(clip.duration_ms for clip in self.clips)
