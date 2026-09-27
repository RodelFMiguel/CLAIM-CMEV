"""Overlong job keys and database-refused values never wedge a partition.

SQLite ignores ``VARCHAR(n)``; PostgreSQL refuses a longer value with SQLSTATE 22001,
which SQLAlchemy raises as ``DataError``. ``postgres_lengths`` emulates that refusal on
SQLite for every ``INSERT``/``UPDATE`` value bound to a length-limited column, so these
tests catch what the SQLite suite used to hide. It is an emulation, not a PostgreSQL run.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.exc import DataError

from claim_cmev.contracts.common import ContractError
from claim_cmev.contracts.events import validate_message
from claim_cmev.messaging.consumer import consume_batch
from claim_cmev.messaging.transport import DLQ_TOPIC, Message
from claim_cmev.persistence.tables import assessments, consumed_messages, dead_letters, outbox
from pipeline_helpers import make_claim

TOPIC = "cmev.evt.input-revision-created.v1"
GROUP = "cmev-orchestrator"


class _TooLong(Exception):
    pass


@pytest.fixture
def postgres_lengths(database):
    """Raise ``DataError`` like PostgreSQL when a written string exceeds its column length."""
    refused = []

    def check(conn, cursor, statement, parameters, context, executemany):
        compiled = getattr(context, "compiled", None)
        table = getattr(getattr(compiled, "statement", None), "table", None)
        if table is None or not statement.lstrip().upper().startswith(("INSERT", "UPDATE")):
            return
        for row in context.compiled_parameters:
            for name, value in row.items():
                column = table.c.get(name)  # only written columns; WHERE binds carry suffixed names
                length = getattr(getattr(column, "type", None), "length", None)
                if isinstance(value, str) and length and len(value) > length:
                    refused.append((table.name, name, len(value)))
                    raise DataError(statement, parameters,
                                    _TooLong(f"value too long for type character varying({length})"))

    event.listen(database.engine, "before_cursor_execute", check)
    yield refused
    event.remove(database.engine, "before_cursor_execute", check)


def _count(database, table, *where):
    with database.session() as db:
        return db.execute(select(func.count()).select_from(table).where(*where)).scalar()


def _first_event(pipeline, database, cost_tables):
    make_claim(database, "exclusion_and_supported", cost_tables)
    pipeline.relay()
    return pipeline.broker.messages(TOPIC)[0]


def _overlong(good, target_length=180):
    bad = deepcopy(good.value)
    head, signature = bad["job_key"].rsplit(":", 1)
    bad["job_key"] = head.rsplit(":", 1)[0] + ":" + "t" * target_length + ":" + signature
    bad["dedup_key"] = "e" * 64
    return bad


def test_emulation_leaves_a_normal_claim_untouched(database, cost_tables, pipeline, postgres_lengths):
    make_claim(database, "exclusion_and_supported", cost_tables)
    pipeline.drain()
    assert postgres_lengths == []
    assert _count(database, assessments) == 1 and _count(database, dead_letters) == 0


def test_emulation_refuses_an_overlong_write(database, postgres_lengths):
    with pytest.raises(DataError):
        with database.session.begin() as db:
            db.execute(insert(consumed_messages).values(dedup_key="a" * 64, consumer_group="g", topic="t",
                                                        job_key="k" * 201, received_at=func.now(), outcome="x"))
    assert postgres_lengths == [("consumed_messages", "job_key", 201)]


def test_overlong_job_key_is_envelope_invalid_and_the_partition_moves_on(database, cost_tables, pipeline, sleeps,
                                                                          postgres_lengths):
    good = _first_event(pipeline, database, cost_tables)
    bad = _overlong(good)
    assert len(bad["job_key"]) > 200
    with pytest.raises(ContractError) as err:
        validate_message(TOPIC, bad)
    assert err.value.reason_code == "envelope_invalid"

    runtime = pipeline.runtime(GROUP)
    consumer = next(c for r, c in pipeline.consumers if r is runtime)
    poison = pipeline.broker.send(TOPIC, good.key, bad)  # behind the valid event, same partition
    tail = pipeline.broker.send(TOPIC, good.key, deepcopy(good.value))
    outcomes = {}
    process = runtime.process
    runtime.process = lambda m: outcomes.setdefault((m.partition, m.offset), process(m))
    consume_batch(runtime, consumer)
    assert outcomes[(poison.partition, poison.offset)] == "dead_lettered"
    assert outcomes[(good.partition, good.offset)] == "processed"
    assert outcomes[(tail.partition, tail.offset)] == "duplicate"
    assert pipeline.broker.lag(GROUP, [TOPIC]) == 0 and sleeps == [] and postgres_lengths == []
    with database.session() as db:
        letter = db.execute(select(dead_letters)).mappings().one()
    assert (letter["reason_code"], letter["dlq_reason"], letter["job_key"], letter["replayable"]) == (
        "envelope_invalid", "envelope_invalid", None, False)
    assert letter["message"]["job_key"] == bad["job_key"]  # the original is preserved, not truncated


def test_database_refused_value_is_permanent_not_retried(database, cost_tables, pipeline, sleeps, postgres_lengths):
    good = _first_event(pipeline, database, cost_tables)
    runtime = pipeline.runtime(GROUP)
    calls = []

    def writes_an_overlong_value(ctx):
        calls.append(1)
        ctx.session.execute(insert(outbox).values(topic="cmev.evt." + "x" * 200, message_key="k", dedup_key="0" * 64,
                                                  payload={}, created_at=ctx.now))

    runtime.handlers[TOPIC] = writes_an_overlong_value
    consumer = next(c for r, c in pipeline.consumers if r is runtime)
    assert consume_batch(runtime, consumer) == 1
    assert calls == [1] and sleeps == []  # one attempt: the same bytes fail the same way every time
    assert pipeline.broker.lag(GROUP, [TOPIC]) == 0
    with database.session() as db:
        letter = db.execute(select(dead_letters)).mappings().one()
    assert (letter["reason_code"], letter["dlq_reason"]) == ("database_value_rejected", "not_retryable")
    assert letter["job_key"] == good.value["job_key"] and letter["dlq_published"]
    assert runtime.process(good) == "duplicate"


def test_a_refused_full_dead_letter_falls_back_to_a_minimal_row(database, cost_tables, pipeline, sleeps):
    good = _first_event(pipeline, database, cost_tables)
    runtime = pipeline.runtime(GROUP)

    def refuse(*args, **kwargs):
        raise ContractError("payload_invalid", "the DLQ message could not be built")

    runtime._dlq_message = refuse
    bad = deepcopy(good.value)
    bad["payload"]["external_reference"] = "A" * 300  # salvageable identity, invalid payload
    message = pipeline.broker.send(TOPIC, good.key, bad)
    consumer = next(c for r, c in pipeline.consumers if r is runtime)
    assert consume_batch(runtime, consumer) == 2
    assert pipeline.broker.lag(GROUP, [TOPIC]) == 0
    with database.session() as db:
        letter = db.execute(select(dead_letters)).mappings().one()
    assert (letter["job_key"], letter["claim_id"], letter["replayable"], letter["dlq_published"]) == (
        None, None, False, False)
    assert letter["dlq_reason"] == "schema_invalid" and letter["reason_text"].startswith("dead letter degraded")
    assert runtime.process(message) == "duplicate"
    pipeline.relay()
    assert pipeline.broker.messages(DLQ_TOPIC) == []
