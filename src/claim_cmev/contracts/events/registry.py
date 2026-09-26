"""JSON Schema registry for the Kafka topics (integration contracts sections 5 and 6.1).

Producers validate before publishing and consumers validate on receipt, using the same
files. ``validate_message`` raises ``ContractError`` with ``envelope_invalid`` (hard
reject, no retry), ``schema_unsupported`` (``0.1.0`` or any other version) or
``payload_invalid``.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import cache
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ..common import SCHEMA_VERSION, ContractError
from .envelope import Envelope, REQUIRED_ENVELOPE_FIELDS

SCHEMA_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SCHEMA_DIR / "examples"
ENVELOPE_SCHEMA = "envelope.v1.schema.json"
TOPICS: tuple[str, ...] = (
    "cmev.evt.input-revision-created.v1",
    "cmev.cmd.parts-segment.v1",
    "cmev.evt.parts-segmented.v1",
    "cmev.cmd.damage-segment.v1",
    "cmev.evt.damage-segmented.v1",
    "cmev.cmd.part-summary.v1",
    "cmev.evt.part-summarised.v1",
    "cmev.cmd.page-read.v1",
    "cmev.evt.page-read.v1",
    "cmev.cmd.line-items-extract.v1",
    "cmev.evt.line-items-extracted.v1",
    "cmev.cmd.pen-marks-detect.v1",
    "cmev.evt.pen-marks-detected.v1",
    "cmev.cmd.consolidate.v1",
    "cmev.evt.assessment-ready.v1",
    "cmev.evt.job-failed.v1",
    "cmev.dlq.v1",
)
MAX_MESSAGE_BYTES = 256 * 1024
"""Producer hard reject (section 6.4). Hitting it means a list needs pagination."""
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_DATA_URI = re.compile(r"^\s*data:[^,]*;base64,", re.IGNORECASE)


def _read(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


@cache
def _registry() -> Registry:
    resources = [(s["$id"], Resource.from_contents(s)) for s in
                 [_read(ENVELOPE_SCHEMA), *(_read(f"{t}.schema.json") for t in TOPICS)]]
    return Registry().with_resources(resources)


def load_schema(topic: str) -> dict[str, Any]:
    """The topic's JSON Schema document (a fresh copy)."""
    if topic not in TOPICS:
        raise ContractError("topic_unknown", topic)
    return _read(f"{topic}.schema.json")


@cache
def _validator(topic: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(topic), registry=_registry())


@cache
def _envelope_validator() -> Draft202012Validator:
    envelope = _read(ENVELOPE_SCHEMA)
    strict = {"$schema": envelope["$schema"], "$ref": envelope["$id"], "unevaluatedProperties": False}
    return Draft202012Validator(strict, registry=_registry())


def _first_error(validator: Draft202012Validator, instance: Any) -> str | None:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    err = errors[0]
    path = "/".join(map(str, err.absolute_path)) or "<root>"
    return f"{path}: {err.message[:300]}"


def _inline_binary(value: Any, path: str = "payload") -> str | None:
    """Find an inline binary blob. Bytes travel as artifact references, never base64."""
    if isinstance(value, str):
        if _DATA_URI.match(value) or _BASE64_RUN.search(value):
            return path
    elif isinstance(value, Mapping):
        for key, item in value.items():
            found = _inline_binary(item, f"{path}/{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _inline_binary(item, f"{path}/{index}")
            if found:
                return found
    return None


def validate_envelope(message: Any) -> Envelope:
    """Validate only the envelope; the payload is not inspected."""
    if not isinstance(message, Mapping):
        raise ContractError("envelope_invalid", "a message is a JSON object")
    missing = [f for f in REQUIRED_ENVELOPE_FIELDS if f not in message]
    if missing:
        raise ContractError("envelope_invalid", f"missing envelope fields {missing}")
    version = message["schema_version"]
    if not isinstance(version, str):
        raise ContractError("envelope_invalid", "schema_version is a string")
    if version != SCHEMA_VERSION:
        raise ContractError("schema_unsupported", f"schema_version {version!r} is not {SCHEMA_VERSION}; never reinterpreted")
    error = _first_error(_envelope_validator(), dict(message))
    if error:
        raise ContractError("envelope_invalid", error)
    try:
        return Envelope.from_message(message)
    except ValueError as exc:
        raise ContractError("envelope_invalid", str(exc).splitlines()[0]) from exc


def validate_message(topic: str, message: Any) -> dict[str, Any]:
    """Validate a complete message for ``topic`` and return it unchanged as a dict."""
    if topic not in TOPICS:
        raise ContractError("topic_unknown", topic)
    validate_envelope(message)
    if "payload" not in message or not isinstance(message["payload"], Mapping):
        raise ContractError("payload_invalid", "payload is a required JSON object")
    blob = _inline_binary(message["payload"])
    if blob:
        raise ContractError("payload_invalid", f"inline binary data at {blob}; use an artifact reference")
    error = _first_error(_validator(topic), dict(message))
    if error:
        raise ContractError("payload_invalid", error)
    size = len(json.dumps(message, separators=(",", ":")).encode())
    if size > MAX_MESSAGE_BYTES:
        raise ContractError("payload_invalid", f"message is {size} bytes, above the {MAX_MESSAGE_BYTES} byte limit")
    return dict(message)


def load_example(topic: str) -> dict[str, Any]:
    """The stored valid example message for ``topic``."""
    if topic not in TOPICS:
        raise ContractError("topic_unknown", topic)
    return json.loads((EXAMPLES_DIR / f"{topic}.json").read_text(encoding="utf-8"))
