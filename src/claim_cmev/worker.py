"""Lean Kafka consumer + outbox relay; all processing is explicitly mocked.

`--local` is an opt-in development/test transport, never used by Compose.
"""
import argparse
from copy import deepcopy
import json
import logging
from pathlib import Path
import time
from sqlalchemy import select
from .runtime import Database, Record, Outbox, setting, get, put, now, digest
from .fixtures import assessment_for

log = logging.getLogger("cmev.worker")
HEARTBEAT = Path("/tmp/cmev-worker-heartbeat")


def process_event(database, event):
    if not isinstance(event, dict) or event.get("schema_version") != "0.2.0" or not all(isinstance(event.get(k), str) and event[k] for k in ("claim_id", "dedup_key", "job_key")) or type(event.get("input_revision")) is not int or event["input_revision"] < 1:
        raise ValueError("schema_unsupported_or_envelope_invalid")
    cid, ir = event["claim_id"], event["input_revision"]
    with database.session.begin() as db:
        # Claim lock serializes reviews and assessments, also across worker replicas.
        row = db.scalar(select(Record).where(Record.key == "claim:" + cid).with_for_update())
        if row is None:
            raise ValueError("unknown_claim")
        if get(db, "consumed:" + event["dedup_key"]):
            return False
        job_row = db.get(Record, "job:" + event["job_key"])
        if job_row is None or job_row.claim_id != cid or job_row.data["input_revision"] != ir:
            raise ValueError("unknown_or_mismatched_job")
        if job_row.data["state"] == "succeeded":
            return False
        inp = get(db, f"input:{cid}:{ir}")
        if inp is None:
            raise ValueError("unknown_input_revision")
        claim = deepcopy(row.data)
        existing = list(db.scalars(select(Record).where(Record.kind == "assessment", Record.claim_id == cid)))
        revision = 1 + max((x.data["assessment_revision"] for x in existing), default=0)
        previous = get(db, f"assessment:{cid}:{inp['base_assessment_revision']}") if inp.get("base_assessment_revision") else None
        result = assessment_for(claim, revision, inp, previous)
        put(db, f"assessment:{cid}:{revision}", "assessment", result, cid)
        prior_review = get(db, f"review:{cid}:{inp['base_assessment_revision']}") if previous else None
        put(db, f"review:{cid}:{revision}", "review", {"review_revision": claim["review_revision"], "assessment_revision": revision,
            "base_assessment_revision": inp.get("base_assessment_revision"),
            "actions": deepcopy(prior_review["actions"]) if prior_review else [], "finalized": False}, cid)
        job = deepcopy(get(db, "job:" + event["job_key"], {}))
        job.update(state="succeeded", attempts=job.get("attempts", 0) + 1, reason_code="fixture_completed", completed_at=now())
        put(db, "job:" + event["job_key"], "job", job, cid)
        if ir == claim["input_revision"]:
            claim.update(assessment_revision=revision, status="in_review", finding_count=len(result["findings"]))
            claim["estimate_row_count"] = len(result["line_items"])
            from decimal import Decimal
            claim["declared_total"] = format(sum((Decimal(r["printed_amount"]) for r in result["line_items"]), Decimal(0)), ".2f") if result["line_items"] else None
            row.data = claim
        put(db, "consumed:" + event["dedup_key"], "consumed", {"processed_at": now()}, cid)
    return True



def record_failure(database, event, reason):
    envelope = event if isinstance(event, dict) else {}
    identity = digest(json.dumps(event, sort_keys=True, default=str))
    with database.session.begin() as db:
        if get(db, "deadletter:" + identity):
            return
        cid = envelope.get("claim_id")
        cid = cid if isinstance(cid, str) else None
        if cid:
            db.scalar(select(Record).where(Record.key == "claim:" + cid).with_for_update())
        job_key = envelope.get("job_key")
        job_row = db.get(Record, "job:" + job_key) if isinstance(job_key, str) else None
        put(db, "deadletter:" + identity, "dead_letter", {"reason_code": reason, "envelope": event, "created_at": now()}, cid)
        if job_row and job_row.claim_id == cid and job_row.data.get("input_revision") == envelope.get("input_revision") and job_row.data["state"] != "succeeded":
            job = job_row.data
            job_row.data = {**job, "state": "dead_lettered", "reason_code": reason, "attempts": job.get("attempts", 0) + 1}
            claim = get(db, "claim:" + cid)
            if claim and claim["input_revision"] == envelope.get("input_revision"):
                put(db, "claim:" + cid, "claim", {**claim, "status": "failed"}, cid)


def local_tick(database):
    with database.session() as db:
        pending = [(row.id, row.payload) for row in db.scalars(select(Outbox).where(Outbox.published == 0).order_by(Outbox.id))]
    for oid, payload in pending:
        try:
            process_event(database, payload)
        except ValueError as exc:
            record_failure(database, payload, str(exc))
        with database.session.begin() as db:
            db.get(Outbox, oid).published = 1
    return len(pending)


def consume_poll(database, producer, consumer):
    # poll advances positions for every partition. Commit only after the complete
    # batch is durably processed, so a failure cannot acknowledge untouched work.
    for messages in consumer.poll(timeout_ms=1000, max_records=10).values():
        for message in messages:
            try:
                process_event(database, message.value)
            except ValueError as exc:
                record_failure(database, message.value, str(exc))
                producer.send("cmev.dlq.v1", value={"reason_code": str(exc), "envelope": message.value}).get(timeout=10)
                log.error("Rejected invalid fixture job: %s", exc)
    consumer.commit()


def run(local=False):
    if setting("FIXTURE_MODE", "true").lower() != "true":
        raise RuntimeError("Only fixture mode is implemented")
    database = Database()
    if local:
        log.warning("Explicit local development transport selected; Kafka is not exercised")
        while True:
            local_tick(database)
            HEARTBEAT.write_text(str(time.time()))
            time.sleep(1)
    from kafka import KafkaConsumer, KafkaProducer
    topic = "cmev.evt.input-revision-created.v1"
    servers = setting("KAFKA_BOOTSTRAP", "localhost:9092", "KAFKA_BOOTSTRAP_SERVERS")
    while True:
        try:
            producer = KafkaProducer(bootstrap_servers=servers, acks="all", value_serializer=lambda v: json.dumps(v).encode())
            consumer = KafkaConsumer(topic, bootstrap_servers=servers, group_id="cmev-fixture-combined-v1", enable_auto_commit=False,
                auto_offset_reset="earliest", value_deserializer=lambda v: json.loads(v.decode()))
            while True:
                with database.session.begin() as db:
                    rows = db.scalars(select(Outbox).where(Outbox.published == 0).order_by(Outbox.id).with_for_update(skip_locked=True).limit(20))
                    for row in rows:
                        producer.send(topic, key=row.payload["claim_id"].encode(), value=row.payload).get(timeout=10)
                        row.published = 1
                consume_poll(database, producer, consumer)
                HEARTBEAT.write_text(str(time.time()))
        except Exception:
            log.exception("Worker transport unavailable; pending work retained")
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run", choices=["run", "healthcheck"])
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    if args.command == "healthcheck":
        raise SystemExit(0 if HEARTBEAT.exists() and time.time() - float(HEARTBEAT.read_text()) < 30 else 1)
    logging.basicConfig(level=logging.INFO)
    run(args.local)


if __name__ == "__main__":
    main()
