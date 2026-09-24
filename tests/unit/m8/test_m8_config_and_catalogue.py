"""M8 rule configuration, the frozen reason-code catalogue and the no-I/O / no-LLM boundary."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import claim_cmev.comparison as comparison
from claim_cmev.comparison import (
    REASON_CODES, catalogue, display_text, is_known, load_rule_config, reason, require_code,
)
from claim_cmev.comparison.additions import ADDITION_RULES
from claim_cmev.comparison.config import DEFAULT_CONFIG_PATH
from claim_cmev.comparison.cost_check import COST_RULE_BY_REASON, LOOKUP_REASON_RULES
from claim_cmev.contracts.common import DAMAGE_CODES, ContractError
from claim_cmev.costs.reference import LOOKUP_REASON_CODES

# The module 08 reason-code table, verbatim: code -> display text.
SPEC_TABLE = {
    "branch_missing": "Waiting for the other input", "branch_failed": "Processing failed, this item was not checked",
    "mark_pending": "Confirm the pen mark on this row", "mark_conflicting": "Two pen marks conflict on this row",
    "mark_unlinked": "A pen mark may belong to this row", "exclusion_confirmed": "Excluded by the surveyor, not checked",
    "row_fields_incomplete": "Some details could not be read", "operation_unresolved": "The repair operation is unclear",
    "extraction_incomplete": "The estimate was only partly read", "page_unreadable": "This page could not be read",
    "part_identity_unresolved": "The part could not be identified",
    "side_unresolved": "The side of the vehicle is unknown", "coverage_inadequate": "Take more pictures of this part",
    "coverage_not_visible": "This part is not in any photograph",
    "coverage_unresolved": "Coverage of this part is unclear", "damage_evidence_uncertain": "The damage evidence is unclear",
    "damage_type_out_of_scope": "This damage type is not supported",
    "damage_supported": "Damage visible in the covering views",
    "no_supported_damage_in_adequate_views": "No supporting damage detected in adequate views",
    "amount_unresolved": "The revised amount is not confirmed", "amount_unreadable": "The amount could not be read",
    "quantity_missing": "The quantity is missing", "quantity_not_one": "Only single-part amounts are compared",
    "currency_unsupported": "Cost comparison supports SGD only",
    "basis_mismatch": "This amount uses a different cost basis",
    "unsupported_combination": "No reference exists for this combination",
    "unknown_vehicle_class": "The vehicle class is unknown", "no_key": "No reference range for this combination",
    "insufficient_support": "Too few reference cases to compare", "range_invalid": "The reference range is not usable",
    "zero_width_interval": "Reference range has a single value", "amount_in_range": "Within the reference range",
    "amount_above_range": "Above the reference range", "amount_below_range": "Below the reference range",
    "check_not_reached": "Not checked", "declaration_incomplete": "Confirm the estimate is complete",
    "addition_proposed": "Damage with no matching estimate row",
    "addition_withheld_identity_unresolved": "Damage found, part not identified",
    "addition_withheld_evidence_uncertain": "Damage evidence unclear",
    "addition_withheld_coverage": "Take more pictures before adding",
    "addition_withheld_ambiguous_row": "An unclear row may already cover this",
    "addition_withheld_unlinked_mark": "Resolve the pen mark first",
    "addition_withheld_pending_exclusion": "Confirm the pen mark first",
    "addition_suppressed_confirmed_exclusion_same_part": "Damage noted on an excluded row",
}


# ---------------------------------------------------------------- catalogue
def test_catalogue_holds_the_spec_table_verbatim():
    for code, text in SPEC_TABLE.items():
        assert REASON_CODES[code].display_text == text and REASON_CODES[code].source == "spec", code
    additions = {c for c, e in REASON_CODES.items() if e.source == "addition"}
    assert additions == set(REASON_CODES) - set(SPEC_TABLE)
    assert additions == {"row_identity_resolved", "marks_clear", "price_change_confirmed", "no_additions_found",
                         "image_branch_failed", "image_branch_missing", "document_branch_failed",
                         "document_branch_missing"}


def test_catalogue_api_renders_fixed_text_and_refuses_unknown_codes():
    assert reason("mark_pending").message == display_text("mark_pending") == "Confirm the pen mark on this row"
    assert is_known("no_key") and not is_known("made_up")
    with pytest.raises(ContractError) as refused:
        require_code("made_up")
    assert refused.value.reason_code == "reason_code_unknown"
    rows = catalogue()
    assert [r["code"] for r in rows] == list(REASON_CODES)
    assert all(isinstance(r["rule_ids"], list) and r["rule_ids"] for r in rows)


def test_rule_maps_and_m7_lookup_codes_are_all_catalogued():
    assert set(LOOKUP_REASON_CODES) == set(LOOKUP_REASON_RULES)
    for code in [*COST_RULE_BY_REASON, *ADDITION_RULES]:
        assert is_known(code), code


# ---------------------------------------------------------------- configuration
def test_default_config_carries_the_proposed_spec_values():
    config = load_rule_config()
    assert config.rules_config_version == "m8-rules/0.1.0"
    assert config.damage.min_observation_confidence == 0.50 and set(config.damage.supported_types) == set(DAMAGE_CODES)
    assert config.additions.min_observation_confidence == 0.60
    assert config.coverage.require_human_confirmation_for_negative is True
    assert (config.cost.money_places, config.cost.normalised_score_places) == (2, 4)
    assert config.explainer.enabled is False and len(config.sha256) == 64


def test_config_path_and_environment_override(tmp_path, monkeypatch):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules_config_version"] = "m8-rules/0.1.1-test"
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert load_rule_config(path).rules_config_version == "m8-rules/0.1.1-test"
    monkeypatch.setenv("CMEV_M8_CONFIG", str(path))
    assert load_rule_config().rules_config_version == "m8-rules/0.1.1-test"


@pytest.mark.parametrize("override", [
    {"identity": {"require_resolved_side": False}},
    {"coverage": {"required_state": "inadequate"}},
    {"cost": {"quantity_must_equal": 2}},
    {"cost": {"rounding": "ROUND_HALF_EVEN"}},
    {"explainer": {"enabled": True}},
    {"damage": {"supported_types": ["dent", "Dent"]}},
    {"damage": {"supported_types": ["dent", "dent"]}},
    {"additions": {"require_completeness": ["complete", "partial"]}},
    {"damage": {"min_observation_confidence": 1.5}},
    {"unexpected": 1},
])
def test_config_refuses_unsafe_or_unknown_values(override):
    with pytest.raises(ValidationError):
        load_rule_config().with_overrides(override)


# ---------------------------------------------------------------- purity and the no-LLM rule
PURE_MODULES = ("adapter", "additions", "cost_check", "inputs", "lineage", "reason_codes")
FORBIDDEN_IMPORTS = {"os", "pathlib", "socket", "time", "random", "requests", "httpx", "urllib", "kafka", "boto3",
                     "sqlalchemy", "anthropic", "openai", "transformers", "langchain", "torch"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_decision_modules_do_no_io_read_no_clock_and_import_no_language_model():
    package = Path(comparison.__file__).parent
    for name in PURE_MODULES:
        source = (package / f"{name}.py").read_text(encoding="utf-8")
        assert not _imports(package / f"{name}.py") & FORBIDDEN_IMPORTS, name
        for call in ("datetime.now", "datetime.utcnow", "date.today", "open(", "uuid4"):
            assert call not in source, (name, call)
    every = set().union(*(_imports(p) for p in package.glob("*.py")))
    assert not every & {"anthropic", "openai", "transformers", "langchain", "torch"}
