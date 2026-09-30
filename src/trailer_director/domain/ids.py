"""Identifier formats.

Existence is checked by the dataset validator; these types only guarantee
shape, so a typo such as ``SC7`` or ``MUSIC_03`` is rejected on parse.
"""

from typing import Annotated

from pydantic import StringConstraints

EpisodeId = Annotated[str, StringConstraints(pattern=r"^EP_[A-Z0-9_]+$")]
SceneId = Annotated[str, StringConstraints(pattern=r"^SC\d{2}$")]
DialogueId = Annotated[str, StringConstraints(pattern=r"^DLG_\d{3}$")]
CharacterId = Annotated[str, StringConstraints(pattern=r"^CHAR_[A-Z]+$")]
LocationId = Annotated[str, StringConstraints(pattern=r"^LOC_[A-Z_]+$")]
PropId = Annotated[str, StringConstraints(pattern=r"^PROP_[A-Z_]+_\d{2}$")]
MusicId = Annotated[str, StringConstraints(pattern=r"^MUS_\d{2}$")]
ActorId = Annotated[str, StringConstraints(pattern=r"^ACT_\d{2}$")]
CampaignId = Annotated[str, StringConstraints(pattern=r"^CMP_[A-Z0-9_]+$")]
TerritoryCode = Annotated[str, StringConstraints(pattern=r"^(WORLDWIDE|[A-Z]{2}(-[A-Z]{2,3})?)$")]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
