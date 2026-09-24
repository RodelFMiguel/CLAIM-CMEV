"""Runtime schema: ops job/transport tables, pipeline stage results and assessments.

Revision ID: 0001_runtime_schema
Revises:
Create Date: 2026-09-24

Creates ``ops.jobs``, ``ops.job_attempts``, ``ops.branch_state``, ``ops.consumed_messages``,
``ops.outbox``, ``ops.dead_letters``, ``ops.idempotency_keys``, ``ops.reuse_lineage``,
``pipeline.stage_records`` (insert-only validated contract records),
``assessment.assessments`` (insert-only) and ``assessment.current_pointer``. The generic
``baseline_records`` store for claims, files, inputs, reviews and sessions is created only
when absent, so an existing demonstration volume keeps its rows. Forward-only.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_runtime_schema"
down_revision = None
branch_labels = None
depends_on = None

Id = sa.BigInteger().with_variant(sa.Integer, "sqlite")
Ts = sa.DateTime(timezone=True)
STAGES = ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")
INSERT_ONLY = (("pipeline", "stage_records"), ("assessment", "assessments"))


def _pk():
    return sa.Column("id", Id, primary_key=True, autoincrement=True)


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    if postgres:
        for schema in ("ops", "pipeline", "assessment"):
            op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    if not sa.inspect(bind).has_table("baseline_records"):
        op.create_table(
            "baseline_records",
            sa.Column("key", sa.String(250), primary_key=True),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("claim_id", sa.String(26)),
            sa.Column("data", sa.JSON, nullable=False))
        op.create_index("ix_baseline_records_kind", "baseline_records", ["kind"])
        op.create_index("ix_baseline_records_claim_id", "baseline_records", ["claim_id"])

    op.create_table(
        "jobs", _pk(),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("claim_id", sa.String(26), nullable=False),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("version_signature", sa.String(8), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("attempt_epoch", sa.Integer, nullable=False),
        sa.Column("versions", sa.JSON, nullable=False),
        sa.Column("command", sa.JSON),
        sa.Column("result_ref", sa.JSON),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("last_error", sa.JSON),
        sa.Column("created_at", Ts, nullable=False),
        sa.Column("updated_at", Ts, nullable=False),
        sa.UniqueConstraint("job_key", name="uq_jobs_job_key"),
        sa.UniqueConstraint("claim_id", "input_revision", "task", "target", "version_signature",
                            name="uq_jobs_identity"),
        sa.CheckConstraint("state IN ('pending','dispatched','running','succeeded','failed','dead_lettered',"
                           "'not_required')", name="ck_jobs_state"),
        schema="ops")
    op.create_index("ix_jobs_claim_revision", "jobs", ["claim_id", "input_revision"], schema="ops")

    op.create_table(
        "job_attempts", _pk(),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("attempt_epoch", sa.Integer, nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("partition", sa.Integer),
        sa.Column("offset", sa.BigInteger),
        sa.Column("consumer_group", sa.String(80), nullable=False),
        sa.Column("container", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(128)),
        sa.Column("started_at", Ts, nullable=False),
        sa.Column("finished_at", Ts),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(80)),
        sa.ForeignKeyConstraint(["job_key"], ["ops.jobs.job_key"], name="fk_job_attempts_job"),
        schema="ops")
    op.create_index("ix_job_attempts_job", "job_attempts", ["job_key"], schema="ops")

    stage_check = " AND ".join(f"{s} IN ('pending','running','done','failed','not_required')" for s in STAGES)
    op.create_table(
        "branch_state", _pk(),
        sa.Column("claim_id", sa.String(26), nullable=False),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("expected_photos", sa.Integer, nullable=False),
        sa.Column("expected_pages", sa.Integer, nullable=False),
        *(sa.Column(stage, sa.String(16), nullable=False) for stage in STAGES),
        sa.Column("reasons", sa.JSON, nullable=False),
        sa.Column("failures", sa.JSON, nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("cost_table_version", sa.String(128), nullable=False),
        sa.Column("review_revision", sa.Integer),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("consolidate_emitted_at", Ts),
        sa.Column("consolidate_job_key", sa.String(200)),
        sa.Column("created_at", Ts, nullable=False),
        sa.Column("updated_at", Ts, nullable=False),
        sa.UniqueConstraint("claim_id", "input_revision", name="uq_branch_state_revision"),
        sa.CheckConstraint(stage_check, name="ck_branch_state_stages"),
        schema="ops")

    op.create_table(
        "consumed_messages", _pk(),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("consumer_group", sa.String(80), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("job_key", sa.String(200)),
        sa.Column("received_at", Ts, nullable=False),
        sa.Column("completed_at", Ts),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("result_ref", sa.JSON),
        sa.UniqueConstraint("consumer_group", "dedup_key", name="uq_consumed_group_dedup"),
        schema="ops")

    op.create_table(
        "outbox", _pk(),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("message_key", sa.String(64), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("job_key", sa.String(200)),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", Ts, nullable=False),
        sa.Column("published_at", Ts),
        schema="ops")
    op.create_index("ix_outbox_unpublished", "outbox", ["published_at", "id"], schema="ops")

    op.create_table(
        "dead_letters", _pk(),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("consumer_group", sa.String(80), nullable=False),
        sa.Column("dedup_key", sa.String(64)),
        sa.Column("job_key", sa.String(200)),
        sa.Column("claim_id", sa.String(26)),
        sa.Column("input_revision", sa.Integer),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("dlq_reason", sa.String(40), nullable=False),
        sa.Column("reason_text", sa.Text, nullable=False),
        sa.Column("message", sa.JSON, nullable=False),
        sa.Column("failure_history", sa.JSON, nullable=False),
        sa.Column("replayable", sa.Boolean, nullable=False),
        sa.Column("dlq_published", sa.Boolean, nullable=False),
        sa.Column("created_at", Ts, nullable=False),
        sa.Column("replayed_at", Ts),
        schema="ops")

    op.create_table(
        "idempotency_keys", _pk(),
        sa.Column("claim_id", sa.String(26), nullable=False),
        sa.Column("endpoint", sa.String(300), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON, nullable=False),
        sa.Column("created_at", Ts, nullable=False),
        sa.UniqueConstraint("claim_id", "endpoint", "idempotency_key", name="uq_idempotency_scope"),
        schema="ops")

    op.create_table(
        "reuse_lineage", _pk(),
        sa.Column("claim_id", sa.String(26), nullable=False),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("source_input_revision", sa.Integer, nullable=False),
        sa.Column("via_input_revision", sa.Integer, nullable=False),
        sa.Column("source_job_keys", sa.JSON, nullable=False),
        sa.Column("versions", sa.JSON, nullable=False),
        sa.Column("recorded_at", Ts, nullable=False),
        sa.UniqueConstraint("claim_id", "input_revision", "stage", name="uq_reuse_stage"),
        sa.ForeignKeyConstraint(["claim_id", "input_revision"],
                                ["ops.branch_state.claim_id", "ops.branch_state.input_revision"],
                                name="fk_reuse_branch_state"),
        sa.CheckConstraint("source_input_revision < input_revision", name="ck_reuse_earlier_revision"),
        schema="ops")

    op.create_table(
        "stage_records", _pk(),
        sa.Column("record_id", sa.String(128), nullable=False),
        sa.Column("record_kind", sa.String(40), nullable=False),
        sa.Column("claim_id", sa.String(26), nullable=False),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("body", sa.JSON, nullable=False),
        sa.Column("created_at", Ts, nullable=False),
        sa.UniqueConstraint("record_id", name="uq_stage_records_record_id"),
        sa.ForeignKeyConstraint(["job_key"], ["ops.jobs.job_key"], name="fk_stage_records_job"),
        schema="pipeline")
    op.create_index("ix_stage_records_job", "stage_records", ["claim_id", "job_key"], schema="pipeline")

    op.create_table(
        "assessments", _pk(),
        sa.Column("claim_id", sa.String(26), nullable=False),
        sa.Column("assessment_revision", sa.Integer, nullable=False),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("superseded", sa.Boolean, nullable=False),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("cost_table_version", sa.String(128), nullable=False),
        sa.Column("rules_config_version", sa.String(128), nullable=False),
        sa.Column("body", sa.JSON, nullable=False),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("reuse_lineage", sa.JSON, nullable=False),
        sa.Column("created_at", Ts, nullable=False),
        sa.UniqueConstraint("claim_id", "assessment_revision", name="uq_assessment_revision"),
        sa.UniqueConstraint("job_key", name="uq_assessment_job"),
        sa.ForeignKeyConstraint(["job_key"], ["ops.jobs.job_key"], name="fk_assessments_job"),
        sa.CheckConstraint("state IN ('ready','incomplete')", name="ck_assessments_state"),
        schema="assessment")

    op.create_table(
        "current_pointer",
        sa.Column("claim_id", sa.String(26), primary_key=True),
        sa.Column("assessment_revision", sa.Integer, nullable=False),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("finding_count", sa.Integer, nullable=False),
        sa.Column("estimate_row_count", sa.Integer, nullable=False),
        sa.Column("declared_total", sa.String(32)),
        sa.Column("updated_at", Ts, nullable=False),
        schema="assessment")

    # Insert-only is a schema constraint, not application care (technical spec 8.3).
    if postgres:
        op.execute("""CREATE OR REPLACE FUNCTION ops.refuse_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'insert-only table %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME; END $$""")
        for schema, table in INSERT_ONLY:
            op.execute(f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE ON {schema}.{table} "
                       "FOR EACH ROW EXECUTE FUNCTION ops.refuse_mutation()")
    else:
        for _schema, table in INSERT_ONLY:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                           f"BEGIN SELECT RAISE(ABORT, 'insert-only table {table}'); END")


def downgrade() -> None:
    raise NotImplementedError("forward-only: restore from snapshot instead")
