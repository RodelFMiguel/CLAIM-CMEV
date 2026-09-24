"""Kafka message contracts: the envelope model, one JSON Schema per topic and the validator.

Schemas and examples ship inside the package (integration contracts section 6.1).
"""
from .envelope import OPTIONAL_ENVELOPE_FIELDS, REQUIRED_ENVELOPE_FIELDS, Envelope, compute_dedup_key
from .registry import MAX_MESSAGE_BYTES, TOPICS, load_example, load_schema, validate_envelope, validate_message

__all__ = [
    "Envelope", "MAX_MESSAGE_BYTES", "OPTIONAL_ENVELOPE_FIELDS", "REQUIRED_ENVELOPE_FIELDS", "TOPICS",
    "compute_dedup_key", "load_example", "load_schema", "validate_envelope", "validate_message",
]
