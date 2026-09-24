"""Step 4: reviewed-alias vocabulary for parts, operations and printed sides."""
import pytest
import yaml

from claim_cmev.contracts.common import OPERATIONS, PART_CODES, ContractError
from claim_cmev.documents.line_items import load_estimate_vocabulary
from claim_cmev.documents.line_items.text import normalise_text
from claim_cmev.documents.line_items.vocabulary import DEFAULT_VOCABULARY_PATH

from m5_support import VOCABULARY as V


@pytest.mark.parametrize("text, code, side", [
    ("FRT BUMPER", "front-bumper", "unknown"),
    ("Front Bumper Cover Assy.", "front-bumper", "unknown"),
    ("REAR DOOR LH", "back-door", "left"),
    ("FRT DOOR L/H", "front-door", "left"),
    ("FRT DOOR L.H.", "front-door", "left"),
    ("HEADLAMP LH", "headlight", "left"),
    ("FENDER RH", "fender", "right"),
    ("FENDER-RH", "fender", "right"),
    ("MIRROR (R)", "mirror", "right"),
    ("DOOR MIRROR RIGHT HAND", "mirror", "right"),
    ("BONNET", "hood", "unknown"),
    ("GRILLE", "grille", "unknown"),
    ("TAIL LAMP LH", "tail-light", "left"),
    ("QTR PANEL RH", "quarter-panel", "right"),
    ("WINDSCREEN", "windshield", "unknown"),
    (" GRILLE ", "grille", "unknown"),
])
def test_reviewed_aliases_resolve(text, code, side):
    part, stated = V.map_part(text)
    assert (part.code, part.status, part.reason) == (code, "resolved", None)
    assert stated.side == side
    assert stated.source == ("absent" if side == "unknown" else "document_text")


@pytest.mark.parametrize("text, reason, candidates", [
    ("BUMPER", "part_position_unstated", ("front-bumper", "back-bumper")),
    ("DOOR LH", "part_position_unstated", ("front-door", "back-door")),
    ("REAR WINDOW", "part_term_ambiguous", ("back-window", "back-windshield")),
    ("BACK DOOR", "part_term_ambiguous", ("back-door", "trunk")),
    ("HD LAMP BRKT", "part_component_only", ("headlight",)),
    ("MIRROR COVER LH", "part_component_only", ("mirror",)),
    ("FRT BUMPER & GRILLE", "multiple_parts_in_text", ("front-bumper", "grille")),
    ("FRT BUMPER LOWER SECTION", "partial_alias_match", ("front-bumper",)),
    ("FENDER R", "partial_alias_match", ("fender",)),  # bare R may mean rear: kept, not a side
])
def test_ambiguous_text_is_never_resolved(text, reason, candidates):
    part, _ = V.map_part(text)
    assert part.code is None and part.status == "ambiguous"
    assert part.reason == reason
    assert part.candidates == candidates


@pytest.mark.parametrize("text, reason", [
    ("PAINT MATERIALS", "part_alias_not_found"),
    ("TOWING CHARGES", "part_alias_not_found"),
    ("FRNT BUMPR", "part_alias_not_found"),  # misspelling: no fuzzy guess by default
    ("LH", "part_text_missing"),
    ("", "part_text_missing"),
])
def test_unlisted_text_is_unmapped(text, reason):
    part, _ = V.map_part(text)
    assert (part.code, part.status, part.reason) == (None, "unmapped", reason)


@pytest.mark.parametrize("text, side, reason", [
    ("FRT DOOR", "unknown", "side_absent_in_text"),
    ("FRT DOOR LH & RH", "unknown", "multiple_sides_in_text"),
    ("FRT DOOR L", "unknown", "side_text_ambiguous"),
    ("FRT DOOR N/S", "unknown", "side_text_ambiguous"),
    ("FRT DOOR BOTH SIDES", "unknown", "side_text_ambiguous"),
    ("HEADLAMP LEFT", "left", None),
])
def test_side_comes_only_from_explicit_printed_words(text, side, reason):
    _, stated = V.map_part(text)
    assert (stated.side, stated.reason) == (side, reason)


@pytest.mark.parametrize("text, code", [("REPLACE", "replace"), (" REPLACE", "replace"), ("RPR", "repair"),
                                        ("Repair", "repair"), ("PAINT", "paint"), ("RESPRAY", "paint"),
                                        ("RE-SPRAY", "paint"), ("R&I", "other"), ("ALIGNMENT", "other")])
def test_operation_aliases(text, code):
    op = V.map_operation(text)
    assert (op.code, op.status) == (code, "resolved")


def test_operation_ambiguity_and_unmapped():
    rr = V.map_operation("R/R")
    assert (rr.code, rr.status, rr.reason, rr.candidates) == (
        None, "ambiguous", "operation_abbreviation_ambiguous", ("replace", "other"))
    assert V.map_operation("REPLACE PART").status == "ambiguous"
    assert V.map_operation("TBC").status == "unmapped"
    assert V.map_operation("").reason == "operation_text_missing"


def test_vocabulary_codes_are_the_contract_codes():
    data = V.data
    assert set(data.parts) <= set(PART_CODES) and len(data.parts) == len(PART_CODES)
    assert set(data.operations) <= set(OPERATIONS) and "unknown" not in data.operations
    for entry in data.ambiguous_parts:
        assert len(entry.candidates) >= 1
    assert V.version == "m5-estimate-vocabulary/0.1.0" and data.status == "proposed"
    assert data.review.state == "proposed" and data.matching.fuzzy_enabled is False
    assert V.is_sided("front-door") and not V.is_sided("front-bumper") and V.is_sided(None)


def _write_vocab(tmp_path, mutate):
    data = yaml.safe_load(DEFAULT_VOCABULARY_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "vocab.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_duplicate_alias_after_normalisation_is_refused(tmp_path):
    path = _write_vocab(tmp_path, lambda d: d["parts"]["hood"].append("frt-bumper"))
    with pytest.raises(ValueError, match="listed for both"):
        load_estimate_vocabulary(path)


def test_unknown_part_code_and_unknown_operation_alias_are_refused(tmp_path):
    with pytest.raises(ValueError):
        load_estimate_vocabulary(_write_vocab(tmp_path, lambda d: d["parts"].update({"spoiler": ["SPOILER X"]})))
    with pytest.raises(ValueError, match="never resolves"):
        load_estimate_vocabulary(_write_vocab(tmp_path, lambda d: d["operations"].update({"unknown": ["TBC"]})))


def test_taxonomy_version_mismatch_is_refused(tmp_path):
    path = _write_vocab(tmp_path, lambda d: d.update(taxonomy_version="parts-9.9.9"))
    with pytest.raises(ContractError) as err:
        load_estimate_vocabulary(path)
    assert err.value.reason_code == "taxonomy_version_mismatch"


def test_fuzzy_matching_only_ever_suggests(tmp_path):
    path = _write_vocab(tmp_path, lambda d: d["matching"].update(fuzzy_enabled=True, fuzzy_min_ratio=0.85))
    fuzzy = load_estimate_vocabulary(path)
    part, _ = fuzzy.map_part("BONNETT")
    assert (part.code, part.status, part.reason, part.candidates) == (None, "ambiguous", "fuzzy_suggestion",
                                                                      ("hood",))
    assert V.map_part("BONNETT")[0].status == "unmapped"  # off by default
    assert fuzzy.map_part("TOWING")[0].status == "unmapped"


def test_normalisation_is_for_matching_only():
    assert normalise_text("  Frt-Bumper,  Assy. ") == "FRT BUMPER ASSY"
    assert normalise_text("DOOR(L)") == "DOOR (L)"
    assert normalise_text("AMOUNT(S$)") == "AMOUNT (S$)"
