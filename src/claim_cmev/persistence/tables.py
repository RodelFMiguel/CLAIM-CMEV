"""Runtime tables (technical specification sections 8.1, 8.3 and 8.4).

The Alembic revision under ``infra/migrations`` is the DDL authority; these Core tables
mirror it for queries. PostgreSQL uses the named schemas. SQLite, used only by tests and
the ``--local`` development transport, maps every schema onto its default schema through
``SQLITE_SCHEMA_MAP``.

Claims, files, inputs, reviews and sessions still live in the generic
``baseline_records`` store (``claim_cmev.runtime.Record``); relational ``claim.*`` and
``review.*`` tables are future work.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

SCHEMAS = ("ops", "pipeline", "assessment")
SQLITE_SCHEMA_MAP = {schema: None for schema in SCHEMAS}
STAGE_STATES = ("pending", "running", "done", "failed", "not_required")
JOB_STATES = ("pending", "dispatched", "running", "succeeded", "failed", "dead_lettered", "not_required")

metadata = MetaData()
Id = BigInteger().with_variant(Integer, "sqlite")
Ts = DateTime(timezone=True)
JOB_KEY = 200

jobs = Table(
    "jobs", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("job_key", String(JOB_KEY), nullable=False, unique=True),
    Column("claim_id", String(26), nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("task", String(40), nullable=False),
    Column("target", String(128), nullable=False),
    Column("version_signature", String(8), nullable=False),
    Column("stage", String(20), nullable=False),
    Column("state", String(20), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("attempt_epoch", Integer, nullable=False),
    Column("versions", JSON, nullable=False),
    Column("command", JSON),
    Column("result_ref", JSON),
    Column("reason_code", String(80)),
    Column("last_error", JSON),
    Column("created_at", Ts, nullable=False),
    Column("updated_at", Ts, nullable=False),
    UniqueConstraint("claim_id", "input_revision", "task", "target", "version_signature", name="uq_jobs_identity"),
    Index("ix_jobs_claim_revision", "claim_id", "input_revision"),
    schema="ops",
)

job_attempts = Table(
    "job_attempts", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("job_key", String(JOB_KEY), ForeignKey("ops.jobs.job_key"), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("attempt_epoch", Integer, nullable=False),
    Column("topic", String(120), nullable=False),
    Column("partition", Integer),
    Column("offset", BigInteger),
    Column("consumer_group", String(80), nullable=False),
    Column("container", String(120), nullable=False),
    Column("trace_id", String(128)),
    Column("started_at", Ts, nullable=False),
    Column("finished_at", Ts),
    Column("outcome", String(24), nullable=False),
    Column("reason_code", String(80)),
    Index("ix_job_attempts_job", "job_key"),
    schema="ops",
)

branch_state = Table(
    "branch_state", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("claim_id", String(26), nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("expected_photos", Integer, nullable=False),
    Column("expected_pages", Integer, nullable=False),
    *(Column(stage, String(16), nullable=False) for stage in
      ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")),
    Column("reasons", JSON, nullable=False),
    Column("failures", JSON, nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("cost_table_version", String(128), nullable=False),
    Column("review_revision", Integer),
    Column("trigger", String(24), nullable=False),
    Column("consolidate_emitted_at", Ts),
    Column("consolidate_job_key", String(JOB_KEY)),
    Column("created_at", Ts, nullable=False),
    Column("updated_at", Ts, nullable=False),
    UniqueConstraint("claim_id", "input_revision", name="uq_branch_state_revision"),
    schema="ops",
)

consumed_messages = Table(
    "consumed_messages", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("dedup_key", String(64), nullable=False),
    Column("consumer_group", String(80), nullable=False),
    Column("topic", String(120), nullable=False),
    Column("job_key", String(JOB_KEY)),
    Column("received_at", Ts, nullable=False),
    Column("completed_at", Ts),
    Column("outcome", String(24), nullable=False),
    Column("result_ref", JSON),
    UniqueConstraint("consumer_group", "dedup_key", name="uq_consumed_group_dedup"),
    schema="ops",
)

outbox = Table(
    "outbox", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("topic", String(120), nullable=False),
    Column("message_key", String(64), nullable=False),
    Column("dedup_key", String(64), nullable=False),
    Column("job_key", String(JOB_KEY)),
    Column("payload", JSON, nullable=False),
    Column("created_at", Ts, nullable=False),
    Column("published_at", Ts),
    Index("ix_outbox_unpublished", "published_at", "id"),
    schema="ops",
)

dead_letters = Table(
    "dead_letters", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("topic", String(120), nullable=False),
    Column("consumer_group", String(80), nullable=False),
    Column("dedup_key", String(64)),
    Column("job_key", String(JOB_KEY)),
    Column("claim_id", String(26)),
    Column("input_revision", Integer),
    Column("reason_code", String(80), nullable=False),
    Column("dlq_reason", String(40), nullable=False),
    Column("reason_text", Text, nullable=False),
    Column("message", JSON, nullable=False),
    Column("failure_history", JSON, nullable=False),
    Column("replayable", Boolean, nullable=False),
    Column("dlq_published", Boolean, nullable=False),
    Column("created_at", Ts, nullable=False),
    Column("replayed_at", Ts),
    schema="ops",
)

idempotency_keys = Table(
    "idempotency_keys", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("claim_id", String(26), nullable=False),
    Column("endpoint", String(300), nullable=False),
    Column("idempotency_key", String(160), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("response", JSON, nullable=False),
    Column("created_at", Ts, nullable=False),
    UniqueConstraint("claim_id", "endpoint", "idempotency_key", name="uq_idempotency_scope"),
    schema="ops",
)

reuse_lineage = Table(
    "reuse_lineage", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("claim_id", String(26), nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("stage", String(20), nullable=False),
    Column("source_input_revision", Integer, nullable=False),
    Column("via_input_revision", Integer, nullable=False),
    Column("source_job_keys", JSON, nullable=False),
    Column("versions", JSON, nullable=False),
    Column("recorded_at", Ts, nullable=False),
    UniqueConstraint("claim_id", "input_revision", "stage", name="uq_reuse_stage"),
    schema="ops",
)

stage_records = Table(
    "stage_records", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("record_id", String(128), nullable=False, unique=True),
    Column("record_kind", String(40), nullable=False),
    Column("claim_id", String(26), nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("stage", String(20), nullable=False),
    Column("job_key", String(JOB_KEY), ForeignKey("ops.jobs.job_key"), nullable=False),
    Column("body", JSON, nullable=False),
    Column("created_at", Ts, nullable=False),
    Index("ix_stage_records_job", "claim_id", "job_key"),
    schema="pipeline",
)

assessments = Table(
    "assessments", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("claim_id", String(26), nullable=False),
    Column("assessment_revision", Integer, nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("state", String(16), nullable=False),
    Column("superseded", Boolean, nullable=False),
    Column("trigger", String(24), nullable=False),
    Column("job_key", String(JOB_KEY), ForeignKey("ops.jobs.job_key"), nullable=False, unique=True),
    Column("cost_table_version", String(128), nullable=False),
    Column("rules_config_version", String(128), nullable=False),
    Column("body", JSON, nullable=False),
    Column("inputs", JSON, nullable=False),
    Column("reuse_lineage", JSON, nullable=False),
    Column("created_at", Ts, nullable=False),
    UniqueConstraint("claim_id", "assessment_revision", name="uq_assessment_revision"),
    schema="assessment",
)

current_pointer = Table(
    "current_pointer", metadata,
    Column("claim_id", String(26), primary_key=True),
    Column("assessment_revision", Integer, nullable=False),
    Column("input_revision", Integer, nullable=False),
    Column("finding_count", Integer, nullable=False),
    Column("estimate_row_count", Integer, nullable=False),
    Column("declared_total", String(32)),
    Column("updated_at", Ts, nullable=False),
    schema="assessment",
)

__all__ = [
    "JOB_STATES", "SCHEMAS", "SQLITE_SCHEMA_MAP", "STAGE_STATES", "assessments", "branch_state",
    "consumed_messages", "current_pointer", "dead_letters", "idempotency_keys", "job_attempts", "jobs",
    "metadata", "outbox", "reuse_lineage", "stage_records",
]
