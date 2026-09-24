"""Transactional outbox and its relay (technical specification 6.5 steps 6 and 9).

A service never publishes from inside a request or a handler: it writes the validated
message into ``ops.outbox`` in the same transaction as its results. ``relay_once``
publishes unpublished rows in id order and stamps ``published_at``; a crash mid-publish
republishes, which consumer deduplication absorbs. Only one relay publishes at a time
(a PostgreSQL advisory lock), which keeps per-claim order across relay replicas.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import logging
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from ..contracts.events import Envelope, validate_message
from ..persistence.tables import jobs, outbox
from .transport import Producer, TransportUnavailable

log = logging.getLogger("cmev.outbox")
RELAY_LOCK = 72_011_025
DEFAULT_BACKLOG_LIMIT = 1000


def build_message(topic: str, *, claim_id: str, input_revision: int, task: str, versions: Mapping[str, str],
                  provenance: Mapping[str, Any], trace_id: str, occurred_at: datetime, payload: Mapping[str, Any],
                  target: str = "all", attempt_epoch: int = 0, **optional: Any) -> dict[str, Any]:
    """Envelope plus payload with the canonical job key and dedup key, validated for ``topic``."""
    envelope = Envelope.build(topic, claim_id=claim_id, input_revision=input_revision, task=task, versions=versions,
                              provenance=provenance, trace_id=trace_id, occurred_at=occurred_at, target=target,
                              attempt_epoch=attempt_epoch, **optional)
    return validate_message(topic, envelope.message(payload))


def enqueue(session: Session, topic: str, message: Mapping[str, Any], *, now: datetime,
            job_key: str | None = None) -> int:
    """Validate and insert one outbound message; returns the outbox row id."""
    validated = validate_message(topic, message)
    result = session.execute(insert(outbox).values(
        topic=topic, message_key=validated["claim_id"], dedup_key=validated["dedup_key"], job_key=job_key,
        payload=validated, created_at=now, published_at=None))
    return result.inserted_primary_key[0]


def backlog(session: Session, claim_id: str | None = None) -> int:
    query = select(func.count()).select_from(outbox).where(outbox.c.published_at.is_(None))
    if claim_id is not None:
        query = query.where(outbox.c.message_key == claim_id)
    return session.execute(query).scalar_one()


def relay_once(session_factory: Callable[[], Any], producer: Producer, *, clock: Callable[[], datetime],
               limit: int = 100) -> int:
    """Publish up to ``limit`` unpublished rows in id order; returns how many were published.

    A transport failure stops the batch: rows already sent are stamped, the rest stay
    pending for the next tick. Commands whose job is still ``pending`` become
    ``dispatched`` when their row is published.
    """
    sent = 0
    with session_factory.begin() as session:
        if session.get_bind().dialect.name == "postgresql":
            if not session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RELAY_LOCK}).scalar():
                return 0
        rows = session.execute(
            select(outbox).where(outbox.c.published_at.is_(None)).order_by(outbox.c.id).limit(limit)
            .with_for_update(skip_locked=True)).mappings().all()
        for row in rows:
            try:
                producer.send(row["topic"], row["message_key"], row["payload"])
            except TransportUnavailable as exc:
                log.warning("outbox relay paused: %s", exc)
                break
            stamp = clock()
            session.execute(update(outbox).where(outbox.c.id == row["id"]).values(published_at=stamp))
            if row["job_key"]:
                session.execute(update(jobs).where(jobs.c.job_key == row["job_key"], jobs.c.state == "pending")
                                .values(state="dispatched", updated_at=stamp))
            sent += 1
    return sent


__all__ = ["DEFAULT_BACKLOG_LIMIT", "RELAY_LOCK", "backlog", "build_message", "enqueue", "relay_once"]
