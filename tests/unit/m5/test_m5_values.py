"""Step 5: exact decimal parsing, letter/digit confusion, and the no-zero-substitution rule."""
import pytest

from claim_cmev.documents.line_items import parse_decimal

MARKERS = {"S$": "SGD", "SGD": "SGD", "$": None}


@pytest.mark.parametrize("text, value", [
    ("980.00", "980.00"),
    ("1,150.00", "1150.00"),
    ("12,345,678.9", "12345678.9"),
    ("S$ 980.00", "980.00"),
    ("S$980.00", "980.00"),
    ("980.00 SGD", "980.00"),
    ("$420.00", "420.00"),
    ("  640.00 ", "640.00"),
    ("0.00", "0.00"),  # a printed zero is a value, kept exactly
    ("7", "7"),
    ("0980.00", "980.00"),
])
def test_exact_decimal_strings(text, value):
    parsed = parse_decimal(text, currency_markers=MARKERS, expected_currency="SGD")
    assert parsed.value == value and parsed.reason is None
    assert parsed.original_text == text  # the original text is always kept


@pytest.mark.parametrize("text", ["48O.OO", "l50.00", "1S0.00", "8B0.00", "OO", "4I0", "2Z0.00", "|00"])
def test_letter_digit_confusion_is_flagged_never_corrected(text):
    parsed = parse_decimal(text, confidence=0.99)
    assert parsed.value is None
    assert parsed.reason == "ocr_letter_digit_confusion"


def test_confusion_outranks_low_confidence_as_in_the_worked_example():
    parsed = parse_decimal("48O.OO", confidence=0.41, min_confidence=0.5)
    assert (parsed.value, parsed.reason) == (None, "ocr_letter_digit_confusion")


@pytest.mark.parametrize("text, reason", [
    (None, "value_missing"),
    ("", "value_missing"),
    ("   ", "value_missing"),
    ("S$", "value_missing"),
    ("-", "non_numeric_text"),
    ("N/A", "non_numeric_text"),
    ("FOC", "non_numeric_text"),
    ("USD 50.00", "non_numeric_text"),  # an unconfigured currency is not stripped
    ("-50.00", "negative_value"),
    ("(50.00)", "negative_value"),
    ("1.150,00", "ambiguous_separator"),
    ("980,00", "ambiguous_separator"),
    ("1,15.00", "ambiguous_separator"),
    ("980.", "ambiguous_separator"),
    (".50", "ambiguous_separator"),
    ("1 980.00", "ambiguous_separator"),  # two numbers in one cell are never merged
    ("980.001", "too_many_decimal_places"),
    ("900.003", "too_many_decimal_places"),
    ("1234567890123.00", "value_out_of_range"),
])
def test_unreadable_values_are_null_with_a_reason_never_zero(text, reason):
    parsed = parse_decimal(text, currency_markers=MARKERS, expected_currency="SGD")
    assert parsed.value is None
    assert parsed.reason == reason


def test_low_confidence_box_makes_the_value_uncertain():
    assert parse_decimal("980.00", confidence=0.49, min_confidence=0.5).reason == "ocr_low_confidence"
    assert parse_decimal("980.00", confidence=0.5, min_confidence=0.5).value == "980.00"
    assert parse_decimal("980.00", confidence=None).value == "980.00"  # no engine confidence: not below it


def test_currency_marker_conflicting_with_the_claim_is_withheld():
    parsed = parse_decimal("RM 50.00", currency_markers={"RM": "MYR"}, expected_currency="SGD")
    assert (parsed.value, parsed.reason, parsed.marker_currency) == (None, "currency_conflict", "MYR")
    same = parse_decimal("S$ 50.00", currency_markers=MARKERS, expected_currency="SGD")
    assert same.value == "50.00" and same.marker_currency == "SGD"
    bare = parse_decimal("$50.00", currency_markers=MARKERS, expected_currency="SGD")
    assert bare.value == "50.00" and bare.marker_currency is None  # names no currency: no conflict claimed


def test_quantity_units_are_stripped_but_quantity_is_never_defaulted():
    assert parse_decimal("2 PCS", units=("PCS", "PC")).value == "2"
    assert parse_decimal("1pc", units=("PCS", "PC")).value == "1"
    assert parse_decimal("PCS", units=("PCS",)).reason == "non_numeric_text"
    assert parse_decimal(None, units=("PCS",)).value is None


def test_configured_separators_are_honoured():
    parsed = parse_decimal("1.150,00", decimal_separator=",", thousands_separator=".")
    assert parsed.value == "1150.00"
    assert parse_decimal("1,150.00", decimal_separator=",", thousands_separator=".").reason == "ambiguous_separator"


def test_max_decimal_places_is_configurable():
    assert parse_decimal("1.5", max_decimal_places=0).reason == "too_many_decimal_places"
    assert parse_decimal("1", max_decimal_places=0).value == "1"
