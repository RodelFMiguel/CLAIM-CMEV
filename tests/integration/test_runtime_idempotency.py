"""Consumer idempotency, interrupted commits, job keys, dead letters, retries, outbox recovery, ordering.

Integration contracts section 11 and technical specification 6.5/6.6, exercised with
SQLite and the in-memory transport (not Kafka or PostgreSQL).
"""
from __future__ import annotations

from copy import deepcopy

from sqlalchemy import func, select, update

from claim_cmev.contracts.common import make_job_key
from claim_cmev.contracts.events import Envelope, validate_message
from claim_cmev.messaging.consumer import ConsumerRuntime, RetryPolicy, TransientError
from claim_cmev.messaging.outbox import relay_once
from claim_cmev.messaging.transport import DLQ_TOPIC, InMemoryBroker, InMemoryProducer, Message, partition_for
from claim_cmev.orchestration import state
from claim_cmev.orchestration.orchestrator import dispatch_consolidate
from claim_cmev.orchestration.services import local_pipeline
from claim_cmev.persistence.tables import (
    assessments,
    consumed_messages,
    dead_letters,
    job_attempts,
    jobs,
    outbox,
    stage_records,
)
from claim_cmev.costs.reference import list_tables
from claim_cmev.costs.reference.build import main as build_cost_table
from pipeline_helpers import Clock, make_claim, step

EVENT_TOPICS_WITH_RESULTS = ("cmev.cmd.parts-segment.v1", "cmev.cmd.consolidate.v1")


def counts(database):
    with database.session() as db:
        return {name: db.execute(select(func.count()).select_from(table)).scalar()
                for name, table in (("records", stage_records), ("assessments", assessments), ("jobs", jobs),
                                    ("outbox", outbox), ("dead_letters", dead_letters))}


def record_ids(database):
    with database.session() as db:
        return sorted(db.execute(select(stage_records.c.record_id)).scalars())


def test_duplicate_delivery_to_every_consumer_changes_nothing(database, cost_tables, pipeline):
    make_claim(database, "exclusion_and_supported", cost_tables)
    pipeline.drain()
    before, ids = counts(database), record_ids(database)
    assert before["assessments"] == 1 and before["dead_letters"] == 0
    processed = {rt.group: rt.counters["processed"] for rt in pipeline.runtimes}
    for runtime, consumer in pipeline.consumers:  # rewind every group to offset 0: full redelivery
        for key in [k for k in pipeline.broker.committed if k[0] == runtime.group]:
            pipeline.broker.committed[key] = 0
        consumer.restart()
    pipeline.drain()
    assert counts(database) == before
    assert record_ids(database) == ids
    for runtime in pipeline.runtimes:
        assert runtime.counters["duplicate"] == processed[runtime.group] > 0, runtime.group
    with database.session() as db:  # the duplicates published nothing new
        assert db.execute(select(func.count()).select_from(outbox).where(outbox.c.published_at.is_(None))).scalar() == 0


def test_interrupted_commit_publishes_once_and_repeats_no_work(database, cost_tables, pipeline):
    make_claim(database, "pending_price_change", cost_tables)
    step(pipeline, ["cmev-orchestrator"])  # fan-out: parts and page commands in the outbox
    pipeline.relay()
    runtime = pipeline.runtime("cmev-worker-parts")
    consumer = next(c for rt, c in pipeline.consumers if rt is runtime)
    batch = consumer.poll()
    assert batch
    for message in batch:  # database commit succeeds ...
        assert runtime.process(message) == "processed"
    consumer.restart()  # ... then the process dies before the offset commit and before the relay
    with database.session() as db:
        attempts = db.execute(select(func.count()).select_from(job_attempts)).scalar()
        pending_events = db.execute(select(outbox.c.dedup_key).where(
            outbox.c.topic == "cmev.evt.parts-segmented.v1", outbox.c.published_at.is_(None))).scalars().all()
    records = record_ids(database)
    assert len(pending_events) == len(batch)
    redelivered = consumer.poll()
    assert [m.offset for m in redelivered] == [m.offset for m in batch]
    assert [runtime.process(m) for m in redelivered] == ["duplicate"] * len(batch)
    consumer.commit()
    assert record_ids(database) == records
    with database.session() as db:
        assert db.execute(select(func.count()).select_from(job_attempts)).scalar() == attempts
    pipeline.relay()
    pipeline.relay()
    published = [m.value["dedup_key"] for m in pipeline.broker.messages("cmev.evt.parts-segmented.v1")]
    assert sorted(published) == sorted(pending_events)  # exactly once each, from the outbox
    pipeline.drain()
    assert counts(database)["assessments"] == 1


def test_job_key_uniqueness_and_changed_version_is_separate_work(database, cost_tables, pipeline):
    cid = make_claim(database, "unphotographed_part", cost_tables)
    pipeline.drain()
    versions = {"summary_config": "fixture-summary/0.0.0", "taxonomy": "parts-1.0.0"}
    assert make_job_key(cid, 1, "part_summary", versions) == make_job_key(cid, 1, "part_summary", dict(versions))
    with database.session.begin() as db:  # dispatching the same job key again creates nothing
        bs = state.branch_row(db, cid, 1)
        before = counts(database)
        first = state.job_row(db, bs["consolidate_job_key"])
        again = dispatch_consolidate(db, bs, rules_config_version=first["versions"]["rules_config"],
                                     provenance={"source_kind": "fixture", "runtime_profile": "lean",
                                                 "producer_service": "cmev-orchestrator"}, now=Clock()())
    assert again == first["job_key"] and counts(database) == before
    with database.session() as db:
        original = db.execute(select(assessments).where(assessments.c.claim_id == cid)).mappings().one()
    other_root = cost_tables  # a second table version, published beside the first and not promoted
    assert build_cost_table(["--seed", "7", "--out", str(other_root)]) == 0
    other = next(t["table_version"] for t in list_tables(other_root)
                 if t["table_version"] != original["cost_table_version"])
    with database.session.begin() as db:
        key = dispatch_consolidate(db, state.branch_row(db, cid, 1), rules_config_version=first["versions"]["rules_config"],
                                   provenance={"source_kind": "fixture", "runtime_profile": "lean",
                                               "producer_service": "cmev-api"}, now=Clock()(), trigger="reassessment",
                                   cost_table_version=other)
    assert key != first["job_key"]
    pipeline.drain()
    with database.session() as db:
        rows = db.execute(select(assessments).where(assessments.c.claim_id == cid)
                          .order_by(assessments.c.assessment_revision)).mappings().all()
    assert [r["cost_table_version"] for r in rows] == [original["cost_table_version"], other]
    assert rows[0]["body"] == original["body"]  # the first result is untouched
    assert rows[1]["job_key"] == key and rows[1]["assessment_revision"] == 2


def test_envelope_invalid_goes_to_dlq_without_retry(database, cost_tables, sleeps):
    pipeline = local_pipeline(database, sleep=sleeps.append, clock=Clock())
    make_claim(database, "pending_price_change", cost_tables)
    pipeline.relay()
    good = pipeline.broker.messages("cmev.evt.input-revision-created.v1")[0]
    runtime = pipeline.runtime("cmev-orchestrator")
    missing = {k: v for k, v in deepcopy(good.value).items() if k != "dedup_key"}
    called = []
    runtime.handlers[good.topic] = lambda ctx: called.append(ctx)
    assert runtime.process(Message(good.topic, good.key, missing, 0, 99)) == "dead_lettered"
    assert runtime.process(Message(good.topic, good.key, "not json at all", 0, 100)) == "dead_lettered"
    assert not called and not sleeps
    with database.session() as db:
        rows = db.execute(select(dead_letters).order_by(dead_letters.c.id)).mappings().all()
    assert [(r["reason_code"], r["dlq_reason"], r["replayable"], r["dlq_published"]) for r in rows] == [
        ("envelope_invalid", "envelope_invalid", False, True), ("envelope_invalid", "envelope_invalid", False, False)]
    pipeline.relay()
    dlq = pipeline.broker.messages(DLQ_TOPIC)
    assert len(dlq) == 1 and validate_message(DLQ_TOPIC, dlq[0].value)["payload"]["dlq_reason"] == "envelope_invalid"


def test_transient_failures_back_off_then_succeed_or_exhaust(database, cost_tables, sleeps):
    pipeline = local_pipeline(database, sleep=sleeps.append, clock=Clock())
    make_claim(database, "pending_price_change", cost_tables)
    step(pipeline, ["cmev-orchestrator"])
    runtime = pipeline.runtime("cmev-worker-ocr")
    original = runtime.handlers["cmev.cmd.page-read.v1"]
    failures = iter([TransientError("ocr_timeout"), TransientError("ocr_timeout")])

    def flaky(ctx):
        error = next(failures, None)
        if error:
            raise error
        return original(ctx)
    runtime.handlers["cmev.cmd.page-read.v1"] = flaky
    step(pipeline, ["cmev-worker-ocr"])
    assert len(sleeps) == 2 and 2.0 <= sleeps[0] <= 2.5 and 8.0 <= sleeps[1] <= 10.0
    with database.session() as db:
        job = db.execute(select(jobs).where(jobs.c.task == "page_read")).mappings().one()
        outcomes = db.execute(select(job_attempts.c.outcome).where(job_attempts.c.job_key == job["job_key"])
                              .order_by(job_attempts.c.id)).scalars().all()
    assert job["state"] == "succeeded" and outcomes == ["failed", "failed", "succeeded"]

    sleeps.clear()
    runtime.handlers["cmev.cmd.page-read.v1"] = lambda ctx: (_ for _ in ()).throw(TransientError("ocr_timeout"))
    runtime.retry = RetryPolicy(max_attempts=3)
    message = pipeline.broker.messages("cmev.cmd.page-read.v1")[0]
    with database.session.begin() as db:  # force a fresh attempt of the same job for the exhaustion path
        db.execute(update(jobs).where(jobs.c.job_key == job["job_key"]).values(state="dispatched"))
        db.execute(consumed_messages.delete().where(consumed_messages.c.job_key == job["job_key"]))
    assert runtime.process(message) == "dead_lettered"
    assert len(sleeps) == 2
    with database.session() as db:
        letter = db.execute(select(dead_letters)).mappings().one()
        assert letter["dlq_reason"] == "retries_exhausted" and len(letter["failure_history"]) == 3
        assert db.execute(select(jobs.c.state).where(jobs.c.job_key == job["job_key"])).scalar() == "dead_lettered"


def test_unpublished_outbox_survives_broker_outage_and_restart(database, cost_tables):
    broker = InMemoryBroker()
    broker.available = False
    make_claim(database, "unphotographed_part", cost_tables)
    assert relay_once(database.session, InMemoryProducer(broker), clock=Clock()) == 0
    with database.session() as db:
        assert db.execute(select(func.count()).select_from(outbox).where(outbox.c.published_at.is_(None))).scalar() == 1
    restarted = local_pipeline(database, clock=Clock(), sleep=lambda _s: None)  # a new process, a healthy broker
    restarted.drain()
    with database.session() as db:
        rows = db.execute(select(outbox.c.dedup_key, outbox.c.published_at)).all()
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 1
    assert all(published for _key, published in rows)
    sent = [m.value["dedup_key"] for topic in {t for t, _p in restarted.broker.logs} for m in
            restarted.broker.messages(topic)]
    assert sorted(sent) == sorted(key for key, _ in rows)  # every row published exactly once


def test_per_key_ordering_with_interleaved_claims(database, cost_tables):
    pipeline = local_pipeline(database, clock=Clock(), sleep=lambda _s: None)
    seen: dict[str, list[tuple[str, int, int]]] = {}
    for runtime in pipeline.runtimes:
        for topic, handler in list(runtime.handlers.items()):
            def wrapped(ctx, _handler=handler):
                seen.setdefault(ctx.envelope.claim_id, []).append((ctx.topic, ctx.envelope.input_revision,
                                                                   len(seen.get(ctx.envelope.claim_id, []))))
                return _handler(ctx)
            runtime.handlers[topic] = wrapped
    claims = [make_claim(database, scenario, cost_tables) for scenario in
              ("pending_price_change", "exclusion_and_supported", "partial_extraction")]
    pipeline.drain()
    for cid in claims:
        partitions = {m.partition for topic in {t for t, _p in pipeline.broker.logs}
                      for m in pipeline.broker.messages(topic) if m.key == cid and topic != DLQ_TOPIC}
        assert len(partitions) == 1 and partition_for("any", cid) in partitions
        order = [topic for topic, _rev, _i in seen[cid]]
        assert order[0] == "cmev.evt.input-revision-created.v1" and order[-1] == "cmev.cmd.consolidate.v1"
        assert order.index("cmev.cmd.parts-segment.v1") < order.index("cmev.evt.parts-segmented.v1") < \
            order.index("cmev.cmd.damage-segment.v1")
        assert order.index("cmev.cmd.line-items-extract.v1") < order.index("cmev.cmd.pen-marks-detect.v1")
    with database.session() as db:
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 3


def test_same_message_built_twice_has_one_identity():
    provenance = {"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "t"}
    args = dict(claim_id="01K6F1XTVRE000000000000000", input_revision=1, task="page_read",
                versions={"ocr": "fixture-ocr/0.0.0"}, provenance=provenance, trace_id="t",
                occurred_at=Clock()(), target="PG1")
    one, two = Envelope.build("cmev.cmd.page-read.v1", **args), Envelope.build("cmev.cmd.page-read.v1", **args)
    retry = Envelope.build("cmev.cmd.page-read.v1", **args, attempt_epoch=1)
    assert one.job_key == two.job_key == retry.job_key
    assert one.dedup_key == two.dedup_key != retry.dedup_key


def test_poison_payload_and_invalid_versions_do_not_block_later_messages(database, cost_tables, pipeline):
    from claim_cmev.messaging.consumer import consume_batch
    make_claim(database, "pending_price_change", cost_tables)
    pipeline.relay()
    good = pipeline.broker.messages("cmev.evt.input-revision-created.v1")[0]
    runtime = pipeline.runtime("cmev-orchestrator")
    seen = []
    runtime.handlers[good.topic] = lambda ctx: seen.append(ctx.envelope.claim_id)
    for number, mutation in enumerate((
        lambda v: v["payload"].update(external_reference="A" * 300),
        lambda v: v.update(versions={"intake": ""}),
        lambda v: v.update(versions={"intake": {"invalid": "nested"}}),
        lambda v: v["payload"].update(external_reference="text " * 60000),
    )):
        bad = deepcopy(good.value)
        mutation(bad)
        message = Message(good.topic, good.key, bad, 0, 900 + number)
        assert runtime.process(message) == "dead_lettered"
        assert runtime.process(message) == "duplicate"
    consumer = next(c for r, c in pipeline.consumers if r is runtime)
    assert consume_batch(runtime, consumer) == 1
    assert seen == [good.value["claim_id"]]
    pipeline.relay()
    letters = pipeline.broker.messages(DLQ_TOPIC)
    assert len(letters) == 4
    for letter in letters:
        assert validate_message(DLQ_TOPIC, letter.value)
    with database.session() as db:
        assert db.execute(select(func.count()).select_from(dead_letters)).scalar() == 4
