"""Exact decimal parsing of printed quantities and amounts (M5 step 5).

Decimal only, never float. Only configured currency markers, quantity units and the
surrounding spaces are stripped; the original text is always returned. A value that
cannot be read exactly is ``None`` with a reason: letter and digit confusions (``48O.OO``)
are flagged, never corrected; zero is never substituted for missing text; a missing
quantity is never one.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

# Letters and marks an OCR engine confuses with digits (O/D/Q -> 0, l/I/| -> 1, S -> 5,
# B -> 8, Z -> 2, G/b -> 6, g/q -> 9). Presence flags the value; nothing is replaced.
CONFUSABLE_CHARACTERS = frozenset("OoDQlIi|!SsBZzGbgq")
MAX_INTEGER_DIGITS = 12  # the contract decimal string allows 12 integer digits


@dataclass(frozen=True)
class ParsedValue:
    value: str | None  # exact nonnegative decimal string, or None
    reason: str | None  # set exactly when value is None
    original_text: str | None
    currency_marker: str | None = None
    marker_currency: str | None = None  # ISO code the marker names, None when it names none


def _strip_affix(text: str, affixes: Sequence[str]) -> tuple[str, str | None]:
    upper = text.upper()
    for affix in sorted(affixes, key=len, reverse=True):
        a = affix.upper()
        if upper.startswith(a):
            return text[len(affix):].strip(), affix
        if upper.endswith(a):
            return text[:len(text) - len(affix)].strip(), affix
    return text, None


def parse_decimal(text: str | None, *, decimal_separator: str = ".", thousands_separator: str = ",",
                  max_decimal_places: int = 2, currency_markers: Mapping[str, str | None] | None = None,
                  units: Sequence[str] = (), confidence: float | None = None, min_confidence: float = 0.5,
                  expected_currency: str | None = None) -> ParsedValue:
    """Parse one printed number exactly, or return ``None`` with the first failing reason.

    Reason order: ``value_missing``, ``ocr_letter_digit_confusion``, ``non_numeric_text``,
    ``negative_value``, ``ambiguous_separator``, ``too_many_decimal_places``,
    ``value_out_of_range``, ``ocr_low_confidence``, ``currency_conflict``.
    """
    markers = dict(currency_markers or {})

    def fail(reason: str, marker: str | None = None) -> ParsedValue:
        return ParsedValue(None, reason, text, marker, markers.get(marker) if marker else None)

    if text is None or not text.strip():
        return fail("value_missing")
    s = unicodedata.normalize("NFKC", text).strip()
    s, marker = _strip_affix(s, list(markers))
    currency = markers.get(marker) if marker else None
    if units:
        for unit in sorted(units, key=len, reverse=True):
            match = re.fullmatch(rf"(.*?)\s*{re.escape(unit)}\.?", s, flags=re.IGNORECASE)
            if match and match.group(1) and re.search(r"\d", match.group(1)):
                s = match.group(1).strip()
                break
    if not s:
        return fail("value_missing", marker)
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    body = s.strip("()").lstrip("-").strip() if negative else s
    separators = {decimal_separator, thousands_separator} - {""}
    odd = {c for c in body if not (c.isdigit() and c.isascii()) and c not in separators and not c.isspace()}
    if not body or not any(c.isdigit() or c in CONFUSABLE_CHARACTERS for c in body):
        return fail("non_numeric_text", marker)  # e.g. "-", "N/A": never read as zero
    if odd:
        if odd <= CONFUSABLE_CHARACTERS:
            return fail("ocr_letter_digit_confusion", marker)
        return fail("non_numeric_text", marker)
    if negative:
        return fail("negative_value", marker)
    if any(c.isspace() for c in body):
        return fail("ambiguous_separator", marker)
    d, t = re.escape(decimal_separator), re.escape(thousands_separator)
    plain = rf"(?P<int>\d+)(?:{d}(?P<frac>\d+))?"
    grouped = rf"(?P<int>\d{{1,3}}(?:{t}\d{{3}})+)(?:{d}(?P<frac>\d+))?" if thousands_separator else None
    match = re.fullmatch(plain, body) or (re.fullmatch(grouped, body) if grouped else None)
    if not match:
        return fail("ambiguous_separator", marker)
    integer = match.group("int").replace(thousands_separator, "") if thousands_separator else match.group("int")
    integer = integer.lstrip("0") or "0"  # same exact value; the contract string allows 12 digits
    fraction = match.group("frac")
    if fraction is not None and len(fraction) > max_decimal_places:
        return fail("too_many_decimal_places", marker)
    if len(integer) > MAX_INTEGER_DIGITS:
        return fail("value_out_of_range", marker)
    if confidence is not None and confidence < min_confidence:
        return fail("ocr_low_confidence", marker)
    if currency is not None and expected_currency is not None and currency != expected_currency:
        return fail("currency_conflict", marker)
    value = integer if fraction is None else f"{integer}.{fraction}"
    Decimal(value)  # exactness guard: always a valid decimal literal here
    return ParsedValue(value, None, text, marker, currency)
