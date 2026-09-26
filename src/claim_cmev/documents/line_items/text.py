"""Text normalisation and phrase lookup shared by header, row-pattern and vocabulary matching.

Normalisation is for matching only: the original text is always kept separately and
published unchanged. It never touches numbers (see ``values``).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import TypeVar

_PARENS = re.compile(r"\(([^()]*)\)")
_SEPARATORS = re.compile(r"[,;:*#\"'_=\-‐-―]+")
T = TypeVar("T")


def normalise_text(text: str) -> str:
    """Upper case, dots deleted, parentheses split off, separators to spaces, whitespace collapsed."""
    value = unicodedata.normalize("NFKC", text).upper().replace(".", "")
    value = _PARENS.sub(lambda m: f" ({m.group(1).strip()}) ", value)
    value = _SEPARATORS.sub(" ", value)
    return " ".join(value.split())


def tokens(text: str) -> tuple[str, ...]:
    return tuple(normalise_text(text).split())


def starts_with_phrase(text: str, phrase: str) -> bool:
    """``phrase`` is a whole-word prefix of ``text`` (both already normalised)."""
    return text == phrase or text.startswith(phrase + " ")


def find_phrases(words: Sequence[str], table: Mapping[tuple[str, ...], T]) -> list[tuple[int, int, T]]:
    """Non-overlapping occurrences of table phrases in ``words``, longest first, left to right.

    Returns ``(start, end, value)`` with ``words[start:end]`` equal to the phrase.
    """
    longest = max((len(k) for k in table), default=0)
    found, i = [], 0
    while i < len(words):
        for size in range(min(longest, len(words) - i), 0, -1):
            key = tuple(words[i:i + size])
            if key in table:
                found.append((i, i + size, table[key]))
                i += size
                break
        else:
            i += 1
    return found
