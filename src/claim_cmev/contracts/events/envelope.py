"""The shared Kafka message envelope (integration contracts section 5.2).

``dedup_key = sha256(topic + "|" + job_key + "|" + attempt_epoch)`` in lowercase hex.
It is stable across redeliveries of one logical message; a deliberate operator retry
mints a new ``attempt_epoch`` and therefore a new key.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from ..common import (
    SCHEMA_VERSION,
    ClaimId,
    ContractModel,
    Provenance,
    Revision,
    SchemaVersion,
    UtcDatetime,
    Versions,
    dedup_key,
    make_job_key,
)

JobTask = Literal["intake", "parts_segment", "damage_segment", "part_summary", "page_read",
                  "line_items_extract", "pen_marks_detect", "consolidate"]
REQUIRED_ENVELOPE_FIELDS = ("schema_version", "claim_id", "input_revision", "job_key", "versions",
                            "provenance", "occurred_at", "dedup_key", "trace_id")
OPTIONAL_ENVELOPE_FIELDS = ("assessment_revision", "attempt", "causation_id")
JOB_KEY_PATTERN = (r"^[0-9A-HJKMNP-TV-Z]{26}:[1-9][0-9]*:(intake|parts_segment|damage_segment|part_summary|"
                   r"page_read|line_items_extract|pen_marks_detect|consolidate):[A-Za-z0-9_.\-]+:[0-9a-f]{8}$")

compute_dedup_key = dedup_key
"""``sha256(topic|job_key|attempt_epoch)``; see the module docstring."""


class Envelope(ContractModel):
    """Envelope fields only. A message is these fields plus ``payload``."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    claim_id: ClaimId
    input_revision: Revision
    job_key: str = Field(pattern=JOB_KEY_PATTERN)
    versions: Versions
    provenance: Provenance
    occurred_at: UtcDatetime
    dedup_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    trace_id: str = Field(min_length=1, max_length=128)
    assessment_revision: Revision | None = None
    attempt: int = Field(default=1, ge=1)
    causation_id: str | None = None

    @model_validator(mode="after")
    def _job_key_matches(self) -> Envelope:
        if not self.job_key.startswith(f"{self.claim_id}:{self.input_revision}:"):
            raise ValueError("job_key must start with the envelope's claim_id and input_revision")
        return self

    @classmethod
    def from_message(cls, message: Mapping[str, Any]) -> Envelope:
        return cls.model_validate({k: v for k, v in message.items() if k != "payload"})

    @classmethod
    def build(cls, topic: str, *, claim_id: str, input_revision: int, task: str, versions: Mapping[str, str],
              provenance: Provenance | Mapping[str, Any], trace_id: str, occurred_at: datetime,
              target: str = "all", attempt_epoch: int = 0, **optional: Any) -> Envelope:
        """Mint an envelope with the canonical job key and dedup key."""
        job_key = make_job_key(claim_id, input_revision, task, versions, target)
        return cls(claim_id=claim_id, input_revision=input_revision, job_key=job_key, versions=dict(versions),
                   provenance=provenance, occurred_at=occurred_at, dedup_key=dedup_key(topic, job_key, attempt_epoch),
                   trace_id=trace_id, **optional)

    def message(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """JSON-ready message dict: envelope fields plus ``payload`` (validate it with the registry)."""
        return {**self.model_dump(mode="json"), "payload": dict(payload)}
