"""Fixture bundles, record round trips, null reasons, v1 rejection and the no-LLM static check."""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import claim_cmev
from claim_cmev.contracts import ClaimFile, ClaimInput, LineItem, PenMark, to_decimal
from claim_cmev.contracts.fixtures import SCENARIOS, FixtureBundle, all_fixture_bundles, fixture_bundle
from claim_cmev.contracts.imaging import ImageDamageObservation
from contract_factories import CLAIM, SCOPE, artifact, line_item, observation, pen_mark, sha


@pytest.fixture(scope="module")
def bundles():
    return all_fixture_bundles()


def test_scenarios(bundles):
    assert SCENARIOS == ("pending_price_change", "exclusion_and_supported", "partial_extraction", "unphotographed_part")
    assert set(bundles) == set(SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_bundles_validate_round_trip_and_are_fixtures(scenario, bundles):
    bundle = bundles[scenario]
    assert all(r.provenance.source_kind == "fixture" for r in bundle.records())
    assert all(o.side == "unknown" for o in bundle.observations)
    again = FixtureBundle.model_validate_json(bundle.model_dump_json())
    assert again == bundle
    assert bundle.vehicle_class in ("sedan_standard", "suv_crossover") and bundle.currency == "SGD"


def test_bundles_are_deterministic_per_claim_and_revision():
    first = fixture_bundle("exclusion_and_supported", CLAIM, 3)
    assert first == fixture_bundle("exclusion_and_supported", CLAIM, 3)
    other = fixture_bundle("exclusion_and_supported", CLAIM, 4)
    assert {i.entry_id for i in first.line_items}.isdisjoint({i.entry_id for i in other.line_items})
    assert other.claim.previous_input_revision == 3
    with pytest.raises(KeyError):
        fixture_bundle("happy_path", CLAIM)


def test_required_scenario_content(bundles):
    pending = bundles["pending_price_change"]
    bumper = pending.line_items[0]
    assert pending.pen_marks[0].state == "pending" and pending.pen_marks[0].mark_type == "price_change"
    assert bumper.printed_line_amount == "1150.00" and bumper.effective_price is None
    assert bumper.effective_price_reason == "price_change_pending"
    assert any(i.side == "unknown" for i in pending.line_items)

    supported = bundles["exclusion_and_supported"]
    exclusion, repriced = supported.pen_marks
    assert exclusion.mark_type == "exclusion" and exclusion.state == "confirmed"
    row = next(i for i in supported.line_items if i.entry_id == repriced.entry_id)
    assert (row.effective_price, row.effective_price_source) == ("980.00", "surveyor_entry")
    slot = next(c for c in supported.coverage if (c.part_code, c.side) == (row.part_code, row.side))
    assert slot.state == "adequate" and slot.coverage_confirmation_id
    assert any(s.identity_status == "resolved" and s.part_code == row.part_code for s in supported.part_summaries)
    assert any(c.state == "inadequate" and "cropped_at_border" in c.reasons for c in supported.coverage)

    partial = bundles["partial_extraction"]
    assert partial.declaration.state == "partial" and partial.declaration.source == "parser"
    assert partial.pages[0].quality.state == "complete"
    unreadable = partial.line_items[1]
    assert unreadable.printed_line_amount is None and unreadable.original_amount_text == "48O.OO"
    assert partial.pen_marks[0].entry_id is None and len(partial.pen_marks[0].candidate_entry_ids) == 2

    unphotographed = bundles["unphotographed_part"]
    tail = next(c for c in unphotographed.coverage if c.part_code == "tail-light")
    assert (tail.side, tail.state, tail.reasons) == ("left", "not_visible", ["no_accepted_part_mask"])
    assert unphotographed.pen_marks == []


def test_every_slot_has_coverage_and_unknown_side_is_unresolved(bundles):
    for bundle in bundles.values():
        for cov in bundle.coverage:
            if cov.side == "unknown":
                assert cov.state == "unresolved"
            if cov.state == "adequate":
                assert cov.coverage_confirmation_id in {c.confirmation_id for c in bundle.coverage_confirmations}


def test_record_money_is_exact():
    item = LineItem(**line_item(unit_price="1234.567", printed_line_amount="1234.567", effective_price="1234.567"))
    dumped = item.model_dump(mode="json")
    assert dumped["printed_line_amount"] == "1234.567"
    assert to_decimal(LineItem.model_validate(dumped).effective_price) == to_decimal("1234.567")


# --- null carries a reason ---------------------------------------------------------------------
NULL_REASON_CASES = [
    (LineItem, line_item, {"part_code": None, "part_mapping_status": "unmapped"}),
    (LineItem, line_item, {"operation": None, "operation_mapping_status": "unmapped"}),
    (LineItem, line_item, {"quantity": None}),
    (LineItem, line_item, {"unit_price": None}),
    (LineItem, line_item, {"amount_box_norm": None}),
    (LineItem, line_item, {"printed_line_amount": None, "effective_price": None,
                           "effective_price_source": "unresolved", "effective_price_reason": "amount_unreadable"}),
    (LineItem, line_item, {"effective_price": None, "effective_price_source": "unresolved"}),
    (ImageDamageObservation, observation, {"part_code": None, "assignment_status": "unresolved"}),
]


@pytest.mark.parametrize("model,factory,overrides", NULL_REASON_CASES)
def test_null_without_reason_fails(model, factory, overrides):
    with pytest.raises(ValidationError):
        model(**factory(**overrides))


def claim_input(**kw):
    data = {**SCOPE, "external_reference": "OD-2026-004417", "make": "Toyota", "model": "Corolla Altis", "year": 2019,
            "vehicle_class": "sedan_standard", "currency": "SGD", "cost_basis": "single_part_pre_tax_no_discount_v1",
            "created_at": datetime(2026, 9, 22, tzinfo=UTC), "previous_input_revision": None}
    data.update(kw)
    return ClaimInput(**data)


def test_claim_input_rules():
    assert claim_input().vehicle_class == "sedan_standard"
    assert claim_input(vehicle_class="unknown", vehicle_class_reason="class_lookup_missing").vehicle_class == "unknown"
    for overrides in ({"external_reference": None}, {"vehicle_class": "unknown"}, {"previous_input_revision": 1},
                      {"input_revision": 2}, {"claim_id": "CLM-24019"}, {"currency": "sgd"}):
        with pytest.raises(ValidationError):
            claim_input(**overrides)


def test_claim_file_object_key_is_generated():
    uri = "s3://cmev-originals/fixture/abc/ph-1.jpg"
    base = {**SCOPE, "file_id": "ph-1", "kind": "photo", "member_revisions": [1], "original_name": "left_door.jpg",
            "media_type": "image/jpeg", "sha256": sha(uri), "byte_count": 10,
            "object_ref": {**artifact(), "object_uri": uri, "sha256": sha(uri), "media_type": "image/jpeg"},
            "upload_status": "stored", "width": 4032, "height": 3024}
    assert ClaimFile(**base).kind == "photo"
    with pytest.raises(ValidationError, match="generated"):
        ClaimFile(**{**base, "object_ref": {**base["object_ref"], "object_uri": "s3://cmev-originals/left_door.jpg"}})
    with pytest.raises(ValidationError):
        ClaimFile(**{**base, "width": None})


# --- v1 (0.1.0) rejection ------------------------------------------------------------------------
@pytest.mark.parametrize("model,data", [
    (LineItem, line_item(schema_version="0.1.0")),
    (PenMark, pen_mark(schema_version="0.1.0")),
    (ImageDamageObservation, observation(schema_version="0.1.0")),
])
def test_v1_payloads_are_rejected(model, data):
    with pytest.raises(ValidationError, match="schema_unsupported"):
        model(**data)


def test_v1_declared_repair_entry_is_not_a_line_item():
    v1_row = {**SCOPE, "entry_id": "e1", "declared_amount": "980.00", "part_code": "front-bumper"}
    with pytest.raises(ValidationError):
        LineItem(**v1_row)


# --- no language model in the decision path -----------------------------------------------------
BANNED_MODULES = ("openai", "anthropic", "langchain", "langchain_core", "langchain_openai", "llama_index", "litellm",
                  "cohere", "mistralai", "ollama", "google.generativeai", "google.genai", "vllm", "together", "groq")
BANNED_NAMES = {"pipeline", "AutoModelForCausalLM", "AutoModelForSeq2SeqLM", "TextGenerationPipeline"}


def _violations(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [(a.name, None) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [(node.module, a.name) for a in node.names]
        else:
            continue
        for module, name in names:
            if any(module == b or module.startswith(b + ".") for b in BANNED_MODULES):
                found.append(f"{path}: {module}")
            if module.split(".")[0] == "transformers" and name in BANNED_NAMES:
                found.append(f"{path}: transformers.{name}")
    return found


def test_no_language_model_client_in_the_decision_path():
    package = Path(claim_cmev.__file__).resolve().parent
    files = [p for p in package.rglob("*.py") if "explainer" not in p.parts]
    assert files
    violations = [v for p in files for v in _violations(p)]
    assert not violations, violations


def test_the_static_check_detects_a_client(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import anthropic\nfrom transformers import pipeline\nfrom openai.types import Model\n")
    assert len(_violations(probe)) == 3
