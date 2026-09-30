from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable record that rejects unknown fields, so typos in source data surface as errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def ensure_unique(values: Iterable[str], field_name: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate entries in {field_name}: {', '.join(duplicates)}")
