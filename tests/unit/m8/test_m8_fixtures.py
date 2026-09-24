"""End-to-end M8 runs over every validated contract fixture scenario.

Fixture bundles are hand-authored branch outputs, never model output; these tests check
that the rules produce the specified outcomes from them, not that any model is accurate.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claim_cmev.comparison import (
    REASON_CODES, bundle_versions, consolidate, load_rule_config, request_from_bundle,
)
from claim_cmev.contracts.costs import CostKey
from claim_cmev.contracts.events import Envelope, validate_message
from claim_cmev.contracts.fixtures import SCENARIOS, all_fixture_bundles, fixture_bundle
from claim_cmev.costs.reference import PinnedCostTable, load_table
from claim_cmev.costs.reference.build import run as build_m7_table

from m8_support import codes, emitted_codes, range_row

CONFIG = load_rule_config()
CREATED = datetime(2026, 9, 24, 9, 30, tzinfo=UTC)
FIXTURE_TABLE = "t-fixture-1"
RANGES = PinnedCostTable.from_rows(FIXTURE_TABLE, [
    range_row("r-fb-replace-sedan", "front-bumper", "replace", "620.00", "1020.00", table=FIXTURE_TABLE),
    range_row("r-fd-repair-suv", "front-door", "repair", "380.00", "620.00", vehicle_class="suv_crossover",
              table=FIXTURE_TABLE),
], min_independent_support=5)

# Per row, in estimate order: (overall_result, row_state, reason codes, deciding rule)
EXPECTED = {
    "pending_price_change": {
        "rows": [("insufficient_evidence", "active", ["mark_pending", "amount_unresolved"], "R2"),
                 ("insufficient_evidence", "active", ["side_unresolved"], "R5"),
                 ("insufficient_evidence", "active", ["side_unresolved"], "R5")],
        "missing": ("insufficient", ["addition_withheld_identity_unresolved"]),
        "additions": {"proposed": 0, "withheld": 2, "suppressed": 0}},
    "exclusion_and_supported": {
        "rows": [("not_evaluated", "excluded", ["exclusion_confirmed",
                                                "addition_suppressed_confirmed_exclusion_same_part"], "R3"),
                 ("ok", "active", ["amount_in_range"], "R12"),
                 ("insufficient_evidence", "active", ["coverage_inadequate"], "R6")],
        "missing": ("failed", ["addition_proposed"]),
        "additions": {"proposed": 1, "withheld": 0, "suppressed": 1}},
    "partial_extraction": {
        "rows": [("insufficient_evidence", "active", ["mark_unlinked", "amount_unresolved"], "R2"),
                 ("insufficient_evidence", "active", ["mark_unlinked", "amount_unresolved"], "R2"),
                 ("insufficient_evidence", "active", ["row_fields_incomplete", "operation_unresolved"], "R4")],
        "missing": ("not_evaluated", ["declaration_incomplete"]),
        "additions": {"proposed": 0, "withheld": 0, "suppressed": 0}},
    "unphotographed_part": {
        "rows": [("insufficient_evidence", "active", ["coverage_not_visible"], "R6"),
                 ("ok", "active", ["amount_in_range"], "R12"),
                 ("insufficient_evidence", "active", ["side_unresolved"], "R5")],
        "missing": ("insufficient", ["addition_withheld_identity_unresolved"]),
        "additions": {"proposed": 0, "withheld": 1, "suppressed": 0}},
}


def run(bundle, ranges=RANGES, table_version=FIXTURE_TABLE, **kw):
    request = request_from_bundle(bundle, assessment_revision=1, created_at=CREATED, cost_table_version=table_version,
                                  rules_config_version=CONFIG.rules_config_version, **kw)
    return consolidate(request, config=CONFIG, ranges=ranges)


@pytest.fixture(scope="module")
def bundles():
    return all_fixture_bundles()


def test_every_scenario_has_an_expectation():
    assert set(EXPECTED) == set(SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_fixture_scenario_outcomes(bundles, scenario):
    bundle, expected = bundles[scenario], EXPECTED[scenario]
    result = run(bundle)
    assessment = result.assessment
    assert assessment.state == "ready" and assessment.provenance.source_kind == "fixture"
    by_entry = {f.entry_id: f for f in assessment.findings}
    for item, (overall, row_state, reason_codes, rule) in zip(bundle.line_items, expected["rows"], strict=True):
        f = by_entry[item.entry_id]
        assert (f.overall_result, f.row_state, codes(f), result.outcome_rules[item.entry_id]) == (
            overall, row_state, reason_codes, rule), item.original_part_text
    check = assessment.missing_repairs_check
    assert (check.result, codes(check)) == expected["missing"]
    counts = {"proposed": result.proposed_addition_count, "withheld": result.withheld_addition_count,
              "suppressed": result.suppressed_addition_count}
    assert counts == expected["additions"]
    assert emitted_codes(result) <= set(REASON_CODES)


def test_pending_price_change_fixture_never_uses_the_printed_amount(bundles):
    bundle = bundles["pending_price_change"]
    row = bundle.line_items[0]
    assert row.printed_line_amount == "1150.00"
    f = next(f for f in run(bundle).assessment.findings if f.entry_id == row.entry_id)
    assert f.overall_result == "insufficient_evidence" and f.overall_result != "ok"
    assert f.cost_check.reason_code == "amount_unresolved" and f.cost_check.amount is None
    assert "1150" not in f.cost_check.model_dump_json()
    assert f.mark_state_check.detail["effective_price_reason"] == "price_change_pending"


def test_exclusion_fixture_compares_the_confirmed_amount_and_notes_the_excluded_damage(bundles):
    bundle = bundles["exclusion_and_supported"]
    result = run(bundle)
    excluded, repriced, _ = (next(f for f in result.assessment.findings if f.entry_id == i.entry_id)
                             for i in bundle.line_items)
    assert set(excluded.checks.values()) == {"not_evaluated"}
    assert repriced.cost_check.amount == "980.00" and bundle.line_items[1].printed_line_amount == "1150.00"
    (proposed,) = result.assessment.possible_additions
    (suppressed,) = result.assessment.suppressed_additions
    assert (proposed.part_code, proposed.side, proposed.status) == ("hood", "not_applicable", "proposed")
    assert (suppressed.part_code, suppressed.side) == ("back-door", "left")
    assert suppressed.suppressed_by_entry_id == excluded.entry_id


def test_unphotographed_part_is_never_unsupported(bundles):
    result = run(bundles["unphotographed_part"])
    assert result.finding_counts["unsupported"] == 0
    assert any(codes(f) == ["coverage_not_visible"] for f in result.assessment.findings)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_fixture_runs_are_deterministic_including_ids(scenario):
    first = run(fixture_bundle(scenario, "01K6F1XTVRE0000000000000M8"))
    second = run(fixture_bundle(scenario, "01K6F1XTVRE0000000000000M8"))
    assert first.assessment.model_dump_json() == second.assessment.model_dump_json()
    assert first.job_key == second.job_key and first.outcome_rules == second.outcome_rules


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ready_payload_validates_against_the_assessment_ready_schema(bundles, scenario):
    result = run(bundles[scenario])
    a = result.assessment
    envelope = Envelope.build("cmev.evt.assessment-ready.v1", claim_id=a.claim_id, input_revision=a.input_revision,
                              task="consolidate", versions={"rules_config": a.rules_config_version,
                                                            "cost_table": a.cost_table_version},
                              provenance=a.provenance, trace_id="trace-m8", occurred_at=CREATED,
                              assessment_revision=a.assessment_revision)
    assert envelope.job_key == result.job_key
    validate_message("cmev.evt.assessment-ready.v1", envelope.message(result.ready_payload()))


def test_bundle_versions_split_the_two_taxonomies(bundles):
    versions = bundle_versions(bundles["exclusion_and_supported"])
    assert versions["taxonomy_damage"].startswith("damage-cardd-") and versions["taxonomy_parts"] == "parts-1.0.0"
    request = request_from_bundle(bundles["exclusion_and_supported"], assessment_revision=1, created_at=CREATED,
                                  pinned_versions={**versions, "cost_table": FIXTURE_TABLE,
                                                   "rules_config": CONFIG.rules_config_version})
    assert request.cost_table_version == FIXTURE_TABLE
    with pytest.raises(ValueError):
        request_from_bundle(bundles["exclusion_and_supported"], assessment_revision=1, created_at=CREATED)


@pytest.fixture(scope="module")
def m7_table(tmp_path_factory):
    """A real synthetic M7 build, written only under pytest's tmp area."""
    root = tmp_path_factory.mktemp("m8-cost-registry")
    manifest = build_m7_table(["--seed", "20260924", "--out", str(root)])
    return load_table(root, manifest["table_version"])


def test_fixtures_against_a_real_synthetic_m7_table(bundles, m7_table):
    """The consolidator uses M7's pinned table object directly as its RangeLookup."""
    bundle = bundles["exclusion_and_supported"]
    result = run(bundle, ranges=m7_table, table_version=m7_table.table_version)
    repriced = next(f for f in result.assessment.findings if f.entry_id == bundle.line_items[1].entry_id)
    lookup = m7_table.lookup(CostKey(part_code="front-bumper", operation="replace", vehicle_class="sedan_standard",
                                     currency="SGD"))
    assert repriced.photographic_check.result == "passed"
    if lookup.supported:
        assert repriced.cost_check.lower_amount is not None
        assert repriced.overall_result in ("ok", "cost_outlier")
        assert repriced.applied_range_id == lookup.range_id
    else:  # a withheld synthetic range: a green photo check beside a grey cost check
        assert repriced.overall_result == "insufficient_evidence"
        assert repriced.cost_check.reason_code == lookup.reason_code and repriced.applied_range_id is None
    assert result.assessment.cost_table_version == m7_table.table_version
    assert result.assessment.pinned_versions["cost_table"] == m7_table.table_version
