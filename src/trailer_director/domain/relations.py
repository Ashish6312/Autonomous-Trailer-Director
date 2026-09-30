"""Relation kinds shared by the reciprocity check and the relationship-truth rule.

A relation says what the other character is to this one ("paternal_uncle":
the other is my uncle). Relations not listed are not checked.
"""

import re

from trailer_director.domain.story import Character

RELATION_KINDS: dict[str, str] = {
    **dict.fromkeys(("mother", "father", "parent"), "parent"),
    **dict.fromkeys(("daughter", "son", "child"), "child"),
    **dict.fromkeys(
        ("brother", "sister", "sibling", "elder_brother", "younger_brother", "elder_sister", "younger_sister"),
        "sibling",
    ),
    **dict.fromkeys(("wife", "husband", "spouse", "late_wife", "late_husband"), "spouse"),
    **dict.fromkeys(("paternal_uncle", "maternal_uncle", "uncle", "paternal_aunt", "maternal_aunt", "aunt"), "elder"),
    **dict.fromkeys(("niece", "nephew"), "younger"),
    **dict.fromkeys(("brother_in_law", "sister_in_law"), "in_law"),
    **dict.fromkeys(("acquaintance", "family_acquaintance", "friend", "old_friend"), "social"),
}

RECIPROCAL_KIND: dict[str, str] = {
    "parent": "child",
    "child": "parent",
    "sibling": "sibling",
    "spouse": "spouse",
    "elder": "younger",
    "younger": "elder",
    "in_law": "in_law",
    "social": "social",
}

# English kinship words used to address someone, and the relation kind they claim.
ADDRESS_KINDS: dict[str, str] = {
    **dict.fromkeys(("uncle", "aunt", "auntie", "aunty"), "elder"),
    **dict.fromkeys(("mother", "mom", "mum", "father", "dad", "daddy"), "parent"),
    **dict.fromkeys(("brother", "sister"), "sibling"),
    **dict.fromkeys(("son", "daughter"), "child"),
}

_ADDRESS = re.compile(
    r"(?:^|[,;:]\s*)(?:(?P<name>[A-Z][a-z]+)\s+)?(?P<kin>" + "|".join(ADDRESS_KINDS) + r")(?=\s*(?:[,.!?;:]|$))",
    re.IGNORECASE,
)


def kinship_addresses(text: str) -> list[tuple[str | None, str]]:
    """``(name, word)`` for every kinship word used as a form of address ("..., Uncle." / "Bansi Uncle,").

    A word inside a sentence ("your father wrote") is a reference, not an address, and is not returned.
    """
    return [(match.group("name"), match.group("kin").casefold()) for match in _ADDRESS.finditer(text)]


def relation_kind(speaker: Character, other_id: str) -> str | None:
    relation = next((r.relation for r in speaker.relationships if r.character_id == other_id), None)
    return RELATION_KINDS.get(relation) if relation else None
