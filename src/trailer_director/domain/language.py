"""Regional address terms (Kaka, Chacha, Jiji, ...) as modelled in character aliases, and stereotyping language."""

import re
from collections.abc import Iterable

from trailer_director.domain.story import Character

_WORD = re.compile(r"[\w']+")

# Stereotyping or dialect-as-joke phrasings. A pattern list for review, not a bias classifier.
_STEREOTYPING = re.compile(
    r"\b(?:backward|uneducated|illiterate|simple[- ]minded|simple (?:village|rural) folk|yokels?|bumpkins?|"
    r"rustic comedy|(?:funny|comic|silly|thick|heavy) (?:village )?accents?|accents? (?:comedy|humou?r|jokes?)|"
    r"dialect (?:comedy|jokes?|as a joke)|caricatur\w*|stereotyp\w*)\b",
    re.IGNORECASE,
)


def address_terms(characters: Iterable[Character]) -> dict[str, str]:
    """Alias words that are not part of any canonical name, mapped to the character they address.

    'Bansi Kaka' contributes only 'Kaka'; 'Meera beti' contributes only 'beti'.
    """
    characters = list(characters)
    name_words = {word for character in characters for word in character.name.split()}
    return {
        word: character.character_id
        for character in characters
        for alias in character.aliases
        for word in alias.alias.split()
        if word not in name_words
    }


def stereotyping_phrases(text: str) -> list[str]:
    return [match.group(0) for match in _STEREOTYPING.finditer(text)]


def terms_in_text(text: str, terms: Iterable[str]) -> list[str]:
    words = {word.removesuffix("'s") for word in _WORD.findall(text)}  # "Chacha's" uses the term "Chacha"
    return sorted(term for term in terms if term in words)
