"""Topic schemas, examples and envelope rules (integration contracts sections 5, 6 and 11)."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from claim_cmev.contracts import ContractError, PageTransform
from claim_cmev.contracts.common import make_job_key, version_signature
from claim_cmev.contracts.events.envelope import REQUIRED_ENVELOPE_FIELDS, Envelope, compute_dedup_key
from claim_cmev.contracts.events.registry import (
    EXAMPLES_DIR,
    SCHEMA_DIR,
    TOPICS,
    load_example,
    load_schema,
    validate_message,
)
from claim_cmev.contracts.imaging import ImageTransform
from contract_factories import CLAIM, transform

INVALID = sorted((EXAMPLES_DIR / "invalid").glob("*.json"))


def test_seventeen_topics_each_with_schema_and_example():
    assert len(TOPICS) == 17 and len(set(TOPICS)) == 17
    for topic in TOPICS:
        schema = load_schema(topic)
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith(f"{topic}.schema.json")
        assert (EXAMPLES_DIR / f"{topic}.json").exists()
    Draft202012Validator.check_schema(json.loads((SCHEMA_DIR / "envelope.v1.schema.json").read_text()))
    assert {p.name for p in SCHEMA_DIR.glob("*.schema.json")} == {f"{t}.schema.json" for t in TOPICS} | {
        "envelope.v1.schema.json"}


@pytest.mark.parametrize("topic", TOPICS)
def test_every_example_validates_and_round_trips(topic):
    message = load_example(topic)
    assert validate_message(topic, message) == message
    again = json.loads(json.dumps(message))
    assert validate_message(topic, again) == message
    envelope = Envelope.from_message(message)
    assert Envelope.model_validate_json(envelope.model_dump_json()) == envelope


def test_money_survives_as_exact_decimal_strings():
    message = load_example("cmev.evt.line-items-extracted.v1")
    amounts = [row["printed_line_amount"] for row in message["payload"]["line_items"]]
    assert amounts == ["980.00", "480.00", "135.00"]
    assert json.loads(json.dumps(message))["payload"]["line_items"][0]["unit_price"] == "980.00"


def test_at_least_one_invalid_example_per_topic():
    covered = {p.name.split("--")[0] for p in INVALID}
    assert covered == set(TOPICS)
    cases = {p.name.split("--")[2] for p in INVALID}
    for needed in ("money-as-number", "inline-base64-blob", "v1-damage-label-corrosion", "unknown-enum-mark-type",
                   "schema-0.1.0", "missing-dedup-key"):
        assert any(needed in case for case in cases)


@pytest.mark.parametrize("path", INVALID, ids=[p.name for p in INVALID])
def test_invalid_examples_are_rejected_with_the_documented_reason(path):
    topic, reason, _case = path.name[:-5].split("--")
    with pytest.raises(ContractError) as err:
        validate_message(topic, json.loads(path.read_text()))
    assert err.value.reason_code == reason


@pytest.mark.parametrize("field", REQUIRED_ENVELOPE_FIELDS)
@pytest.mark.parametrize("topic", TOPICS)
def test_envelope_completeness(topic, field):
    message = load_example(topic)
    del message[field]
    with pytest.raises(ContractError) as err:
        validate_message(topic, message)
    assert err.value.reason_code == "envelope_invalid"


@pytest.mark.parametrize("version", ["0.1.0", "0.3.0", "1.0.0"])
def test_other_schema_versions_are_unsupported_never_reinterpreted(version):
    message = load_example("cmev.evt.line-items-extracted.v1")
    message["schema_version"] = version
    with pytest.raises(ContractError) as err:
        validate_message("cmev.evt.line-items-extracted.v1", message)
    assert err.value.reason_code == "schema_unsupported"


def test_missing_payload_and_unknown_topic():
    message = load_example("cmev.evt.job-failed.v1")
    del message["payload"]
    with pytest.raises(ContractError) as err:
        validate_message("cmev.evt.job-failed.v1", message)
    assert err.value.reason_code == "payload_invalid"
    with pytest.raises(ContractError) as err:
        validate_message("cmev.evt.page-read.v2", load_example("cmev.evt.page-read.v1"))
    assert err.value.reason_code == "topic_unknown"


def test_oversized_message_is_refused():
    message = load_example("cmev.evt.part-summarised.v1")
    message["payload"]["summary_ids"] = [f"ps_{i:08d}-{'x' * 100}" for i in range(2600)]
    with pytest.raises(ContractError, match="byte limit"):
        validate_message("cmev.evt.part-summarised.v1", message)


def test_detector_marks_are_pending_and_damage_side_is_unknown_in_events():
    marks = load_example("cmev.evt.pen-marks-detected.v1")
    assert {m["state"] for m in marks["payload"]["marks"]} == {"pending"}
    damage = load_example("cmev.evt.damage-segmented.v1")
    assert {o["side"] for o in damage["payload"]["observations"]} == {"unknown"}


def test_part_reason_required_when_event_part_is_null():
    message = load_example("cmev.evt.damage-segmented.v1")
    message["payload"]["observations"][1]["part_reason"] = None
    with pytest.raises(ContractError):
        validate_message("cmev.evt.damage-segmented.v1", message)


# --- envelope construction, job keys and dedup keys (section 6.6) ------------------------------
def test_envelope_build_mints_canonical_keys():
    versions = {"parts_model": "parts-segformer-b0/1.2.0", "taxonomy": "parts-1.0.0"}
    topic = "cmev.cmd.parts-segment.v1"
    args = dict(claim_id=CLAIM, input_revision=1, task="parts_segment", versions=versions, target="ph_01JAX7Q1A",
                provenance={"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "tests"},
                trace_id="t1", occurred_at=datetime(2026, 9, 22, tzinfo=UTC))
    first, redelivery = Envelope.build(topic, **args), Envelope.build(topic, **args)
    assert first.job_key == f"{CLAIM}:1:parts_segment:ph_01JAX7Q1A:{version_signature(versions)}"
    assert first.dedup_key == redelivery.dedup_key == compute_dedup_key(topic, first.job_key, 0)
    retry = Envelope.build(topic, **args, attempt_epoch=1)
    assert retry.job_key == first.job_key and retry.dedup_key != first.dedup_key
    changed = Envelope.build(topic, **{**args, "versions": {**versions, "parts_model": "parts-segformer-b0/1.2.1"}})
    assert changed.job_key != first.job_key
    message = first.message(load_example(topic)["payload"])
    assert validate_message(topic, message)["job_key"] == first.job_key


def test_job_key_uniqueness_rule():
    v = {"summary_config": "summary-1.0.0"}
    assert make_job_key(CLAIM, 2, "part_summary", v) == make_job_key(CLAIM, 2, "part_summary", dict(reversed(v.items())))
    assert make_job_key(CLAIM, 2, "part_summary", v) != make_job_key(CLAIM, 3, "part_summary", v)
    assert make_job_key(CLAIM, 2, "page_read", v, "pg_1") != make_job_key(CLAIM, 2, "page_read", v, "pg_2")


def test_envelope_job_key_must_match_claim_and_revision():
    message = load_example("cmev.cmd.parts-segment.v1")
    message["input_revision"] = 2
    with pytest.raises(ContractError) as err:
        validate_message("cmev.cmd.parts-segment.v1", message)
    assert err.value.reason_code == "envelope_invalid"


# --- the event $defs agree with the Pydantic records --------------------------------------------
def _def_validator(name: str) -> Draft202012Validator:
    envelope = json.loads((SCHEMA_DIR / "envelope.v1.schema.json").read_text())
    schema = copy.deepcopy(envelope["$defs"][name])
    schema["$defs"] = envelope["$defs"]
    return Draft202012Validator(schema)


def test_page_transform_record_matches_event_definition():
    record = PageTransform(source_width=3024, source_height=4032, corrected_width=2480, corrected_height=3508,
                           geometry_correction="none", correction_reason="boundary_unreliable")
    assert not list(_def_validator("pageTransform").iter_errors(record.model_dump(mode="json")))
    example = load_example("cmev.evt.page-read.v1")["payload"]["transform"]
    assert PageTransform.model_validate(example).geometry_correction == "perspective"


def test_image_transform_record_matches_event_definition():
    record = ImageTransform(**transform())
    assert not list(_def_validator("imageTransform").iter_errors(record.model_dump(mode="json")))
    example = load_example("cmev.evt.parts-segmented.v1")["payload"]["transform"]
    assert ImageTransform.model_validate(example).exif_orientation == 6


def test_schemas_live_in_the_package():
    assert Path(SCHEMA_DIR).parts[-3:] == ("claim_cmev", "contracts", "events")


def test_long_provenance_and_versions_valid_in_records_are_valid_on_wire():
    from claim_cmev.contracts.common import Provenance
    message = load_example("cmev.evt.page-read.v1")
    provenance = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="p-" * 70,
                            source_dataset_id="d-" * 140, derivation_refs=["r-" * 150])
    message["provenance"] = provenance.model_dump(mode="json")
    message["versions"] = {"code": "v-" * 100}
    validate_message("cmev.evt.page-read.v1", message)


def test_empty_version_value_is_rejected_by_record_and_wire():
    from pydantic import ValidationError, TypeAdapter
    from claim_cmev.contracts.common import Versions
    with pytest.raises(ValidationError):
        TypeAdapter(Versions).validate_python({"code": ""})
    message = load_example("cmev.evt.page-read.v1")
    message["versions"] = {"code": ""}
    with pytest.raises(ContractError):
        validate_message("cmev.evt.page-read.v1", message)
