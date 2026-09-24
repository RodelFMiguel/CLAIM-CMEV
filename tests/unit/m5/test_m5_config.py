"""The versioned layout-family configuration: proposed families, parser keys, strict validation."""
from decimal import Decimal

import pytest
import yaml

from claim_cmev.documents.line_items import load_layout_families
from claim_cmev.documents.line_items.config import DEFAULT_LAYOUT_FAMILIES_PATH

from m5_support import CONFIG


def test_repository_configuration_proposes_three_families_with_spec_defaults():
    assert CONFIG.status == "proposed" and CONFIG.config_version == "m5-layout-families/0.1.0"
    assert [f.family_id for f in CONFIG.families] == [
        "family-a-ruled-grid", "family-b-numbered-rate", "family-c-compact-quote"]
    assert all(f.status == "proposed" for f in CONFIG.families)
    p = CONFIG.parser
    assert (p.method, p.min_matched_columns, p.band_tolerance_frac, p.max_wrap_gap_frac, p.x_tolerance_frac) == (
        "parser", 4, 0.006, 0.010, 0.030)
    assert (p.max_decimal_places, p.amount_tolerance, p.min_box_confidence, p.max_attempts) == (
        2, Decimal("0.01"), 0.50, 3)
    for family in CONFIG.families:
        assert set(family.header.required_columns) == {"description", "amount"}
    assert CONFIG.family("family-c-compact-quote").min_matched(p) == 3
    assert len(CONFIG.sha256) == 64


def _write(tmp_path, mutate):
    data = yaml.safe_load(DEFAULT_LAYOUT_FAMILIES_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "families.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["parser"].update(method="layoutlmv3"), "method"),
    (lambda d: d["parser"].update(amount_tolerance=0.01), "quoted decimal"),
    (lambda d: d["parser"].update(unknown_key=1), "unknown_key"),
    (lambda d: d["families"][0]["header"]["aliases"]["qty"].append("Amount"), "names both"),
    (lambda d: d["families"][0]["header"].update(required_columns=["description", "colour"]), "required_columns"),
    (lambda d: d["families"][0]["header"]["aliases"].pop("amount"), "description and amount"),
    (lambda d: d["families"][0].update(family_id="unsupported"), "reserved"),
    (lambda d: d["families"][1].update(family_id="family-a-ruled-grid"), "unique"),
    (lambda d: d["families"][0]["currency_markers"].update({"RM": "ringgit"}), "ISO"),
    (lambda d: d["families"][0]["numbers"].update(thousands_separator="."), "differ"),
])
def test_invalid_configuration_is_refused(tmp_path, mutate, message):
    with pytest.raises(ValueError, match=message):
        load_layout_families(_write(tmp_path, mutate))


def test_yaml_booleans_are_not_accepted_as_aliases(tmp_path):
    def mutate(d):
        d["families"][1]["header"]["aliases"]["line_no"] = [False]  # what an unquoted NO becomes

    with pytest.raises(ValueError):
        load_layout_families(_write(tmp_path, mutate))


def test_overrides_produce_a_validated_copy():
    tighter = CONFIG.with_overrides({"parser": {"x_tolerance_frac": 0.01}})
    assert tighter.parser.x_tolerance_frac == 0.01 and CONFIG.parser.x_tolerance_frac == 0.03
    assert tighter.sha256 != CONFIG.sha256
