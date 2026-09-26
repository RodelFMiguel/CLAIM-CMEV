"""The one shared consumer runtime (technical specification 6.5 and 6.6).

Every consumer, whatever its module, runs messages through ``ConsumerRuntime.process``:

1. validate the envelope and payload (``validate_message``); a failure goes straight to
   the dead-letter path with ``envelope_invalid`` or ``schema_invalid``, never retried;
2. open one transaction;
3. insert the ``ops.consumed_messages`` row for ``(consumer_group, dedup_key)``; an
   existing row is a duplicate delivery: roll back, publish nothing;
4. for a command, lock the ``ops.jobs`` row (``SELECT ... FOR UPDATE``); a job already
   ``succeeded`` is a duplicate exactly as in step 3;
5. (artifacts: fixture producers write none);
6. the handler writes its result rows and its completion event into ``ops.outbox``, and
   the runtime marks the job ``succeeded``, all in the same transaction;
7. commit; 8. the caller commits offsets after the batch; 9. the relay publishes.

Transient failures retry in place with bounded backoff (injectable ``sleep`` and
``clock``). A permanent failure, or exhausted retries, records ``ops.dead_letters``,
marks the job ``dead_lettered``, and writes ``cmev.evt.job-failed.v1`` and a
``cmev.dlq.v1`` message into the outbox in one transaction.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import random
import re
import socket
import time
from typing import Any

from pydantic import ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.orm import Session

from ..contracts.common import ContractError
from ..contracts.events import Envelope, validate_message
from ..contracts.events.envelope import JOB_KEY_PATTERN
from ..persistence.tables import consumed_messages, dead_letters, job_attempts, jobs
from .outbox import build_message, enqueue
from .transport import DLQ_TOPIC, Consumer, Message, TransportUnavailable

log = logging.getLogger("cmev.consumer")
JOB_FAILED_TOPIC = "cmev.evt.job-failed.v1"
STAGE_TASKS = frozenset({"parts_segment", "damage_segment", "part_summary", "page_read", "line_items_extract",
                         "pen_marks_detect", "consolidate"})
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class TransientError(RuntimeError):
    """Worth retrying in place: a dependency blipped."""

    def __init__(self, reason_code: str, reason_text: str = ""):
        super().__init__(reason_text or reason_code)
        self.reason_code, self.reason_text = reason_code, reason_text or reason_code


class PermanentError(RuntimeError):
    """Retrying cannot help: dead-letter now with a stable reason code."""

    def __init__(self, reason_code: str, reason_text: str = "", *, retryable: bool = False):
        super().__init__(reason_text or reason_code)
        self.reason_code, self.reason_text, self.retryable = reason_code, reason_text or reason_code, retryable


class _Duplicate(Exception):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    """Technical specification 6.6: up to 3 attempts, backoff 2 s, 8 s, 30 s plus jitter."""

    max_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (2.0, 8.0, 30.0)
    jitter: float = 0.25

    def delay(self, attempt: int, rng: random.Random) -> float:
        base = self.backoff_seconds[min(attempt - 1, len(self.backoff_seconds) - 1)]
        return base * (1.0 + rng.uniform(0.0, self.jitter))


@dataclass
class Context:
    """What a handler may touch: one open transaction and the validated message."""

    session: Session
    topic: str
    message: dict[str, Any]
    envelope: Envelope
    group: str
    service: str
    profile: str
    source_kind: str
    now: datetime
    job: Mapping[str, Any] | None = None
    emitted: list[str] = field(default_factory=list)

    @property
    def payload(self) -> dict[str, Any]:
        return self.message["payload"]

    def provenance(self, **extra: Any) -> dict[str, Any]:
        return {"source_kind": self.source_kind, "runtime_profile": self.profile,
                "producer_service": self.service, **extra}

    def emit(self, topic: str, message: Mapping[str, Any], *, job_key: str | None = None) -> None:
        """Write an outgoing message into the outbox inside the handler's transaction."""
        enqueue(self.session, topic, message, now=self.now, job_key=job_key)
        self.emitted.append(message["dedup_key"])


Handler = Callable[[Context], Mapping[str, Any] | None]
DeadLetterHook = Callable[[Session, Mapping[str, Any], str, str, datetime], None]
"""Called with the salvaged identity (claim_id, input_revision, job_key) of an event this group dead-lettered."""


def _redact(text: str) -> str:
    """Reason text leaves no claim content: first line, bounded."""
    return (text.splitlines() or [""])[0][:300]


def _salvage(value: Any) -> dict[str, Any] | None:
    """Envelope fields good enough to route a dead letter, or None."""
    if not isinstance(value, Mapping):
        return None
    claim, revision, job_key = value.get("claim_id"), value.get("input_revision"), value.get("job_key")
    if not (isinstance(claim, str) and _ULID.match(claim) and type(revision) is int and revision >= 1
            and isinstance(job_key, str) and re.match(JOB_KEY_PATTERN, job_key)
            and job_key.startswith(f"{claim}:{revision}:")):
        return None
    trace = value.get("trace_id") if isinstance(value.get("trace_id"), str) and value.get("trace_id") else "untraced"
    return {"claim_id": claim, "input_revision": revision, "job_key": job_key, "trace_id": trace[:128],
            "versions": {"code": "unknown"},
            "dedup_key": value.get("dedup_key") if isinstance(value.get("dedup_key"), str) else None}


class ConsumerRuntime:
    """Idempotent, transactional message processing for one consumer group."""

    def __init__(self, session_factory: Any, group: str, handlers: Mapping[str, Handler], *, service: str,
                 profile: str = "lean", source_kind: str = "fixture", retry: RetryPolicy | None = None,
                 clock: Callable[[], datetime], sleep: Callable[[float], None] = time.sleep,
                 container: str | None = None, on_dead_letter: DeadLetterHook | None = None,
                 is_superseded: Callable[[Session, str, int], bool] | None = None, seed: int = 0):
        self.session_factory, self.group, self.handlers = session_factory, group, dict(handlers)
        self.service, self.profile, self.source_kind = service, profile, source_kind
        self.retry = retry or RetryPolicy()
        self.clock, self.sleep = clock, sleep
        self.container = container or socket.gethostname()
        self.on_dead_letter, self.is_superseded = on_dead_letter, is_superseded
        self.rng = random.Random(seed)
        self.counters: dict[str, int] = {"processed": 0, "duplicate": 0, "dead_lettered": 0, "retried": 0}

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(self.handlers)

    # ------------------------------------------------------------------ entry point
    def process(self, message: Message) -> str:
        """Process one delivery; returns ``processed``, ``duplicate`` or ``dead_lettered``.

        Raises only when the database itself is unreachable, so the caller keeps the
        offset uncommitted and the message is redelivered later.
        """
        topic = message.topic
        if topic not in self.handlers:
            raise ValueError(f"{self.group} does not consume {topic}")
        started = self.clock()
        try:
            validated = validate_message(topic, message.value)
            envelope = Envelope.from_message(validated)
        except ContractError as exc:
            dlq_reason = "envelope_invalid" if exc.reason_code in ("envelope_invalid", "schema_unsupported") \
                else "schema_invalid"
            history = [{"attempt": 1, "occurred_at": started, "reason_code": exc.reason_code,
                        "reason_text": _redact(str(exc))}]
            return self._dead_letter(message, None, exc.reason_code, dlq_reason, history)
        history: list[dict[str, Any]] = []
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                outcome = self._attempt(message, validated, envelope, attempt)
                self.counters[outcome] += 1
                return outcome
            except _Duplicate:
                self.counters["duplicate"] += 1
                return "duplicate"
            except (PermanentError, ContractError, ValidationError) as exc:
                code = getattr(exc, "reason_code", None) or "contract_violation"
                history.append(self._failure(attempt, code, exc))
                self._record_failed_attempt(message, envelope, attempt, code)
                return self._dead_letter(message, envelope, code, "not_retryable", history)
            except (OperationalError, InterfaceError) as exc:
                code = "database_unavailable"
                history.append(self._failure(attempt, code, exc))
                if attempt == self.retry.max_attempts:
                    raise
            except Exception as exc:  # noqa: BLE001 - transient by default, bounded by the policy
                code = exc.reason_code if isinstance(exc, TransientError) else (
                    "result_conflict" if isinstance(exc, IntegrityError) else
                    "transport_unavailable" if isinstance(exc, TransportUnavailable) else "unexpected_error")
                history.append(self._failure(attempt, code, exc))
                self._record_failed_attempt(message, envelope, attempt, code)
            if attempt < self.retry.max_attempts:
                self.counters["retried"] += 1
                self.sleep(self.retry.delay(attempt, self.rng))
        return self._dead_letter(message, envelope, history[-1]["reason_code"], "retries_exhausted", history)

    def _failure(self, attempt: int, code: str, exc: BaseException) -> dict[str, Any]:
        log.warning("%s attempt %s failed with %s", self.group, attempt, code)
        return {"attempt": attempt, "occurred_at": self.clock(), "reason_code": code,
                "reason_text": _redact(getattr(exc, "reason_text", None) or type(exc).__name__)}

    # ------------------------------------------------------------------ one attempt
    def _attempt(self, message: Message, validated: dict[str, Any], envelope: Envelope, attempt: int) -> str:
        with self.session_factory.begin() as session:
            now = self.clock()
            seen = session.execute(select(consumed_messages.c.id).where(
                consumed_messages.c.consumer_group == self.group,
                consumed_messages.c.dedup_key == envelope.dedup_key)).first()
            if seen:
                raise _Duplicate
            try:  # nothing is written before this insert, so a conflict rolls back an empty transaction
                session.execute(insert(consumed_messages).values(
                    dedup_key=envelope.dedup_key, consumer_group=self.group, topic=message.topic,
                    job_key=envelope.job_key, received_at=now, outcome="processing"))
            except IntegrityError as exc:  # a concurrent delivery won the insert
                raise _Duplicate from exc
            job = None
            if message.topic.startswith("cmev.cmd."):
                job = session.execute(select(jobs).where(jobs.c.job_key == envelope.job_key)
                                      .with_for_update()).mappings().first()
                if job is None:
                    raise PermanentError("unknown_job", "no job row exists for this command")
                if job["state"] == "succeeded":
                    raise _Duplicate
                if job["state"] == "not_required":
                    raise PermanentError("job_not_required", "the command names a stage that is not required")
                session.execute(update(jobs).where(jobs.c.job_key == job["job_key"]).values(
                    state="running", attempt_count=jobs.c.attempt_count + 1, updated_at=now))
            ctx = Context(session=session, topic=message.topic, message=validated, envelope=envelope,
                          group=self.group, service=self.service, profile=self.profile,
                          source_kind=self.source_kind, now=now, job=job)
            result_ref = dict(self.handlers[message.topic](ctx) or {})
            session.execute(update(consumed_messages).where(
                consumed_messages.c.consumer_group == self.group,
                consumed_messages.c.dedup_key == envelope.dedup_key).values(
                completed_at=now, outcome="processed", result_ref=result_ref))
            if job is not None:
                session.execute(update(jobs).where(jobs.c.job_key == job["job_key"]).values(
                    state="succeeded", result_ref=result_ref, reason_code=None, updated_at=now))
                self._attempt_row(session, job, message, envelope, attempt, "succeeded", None, now)
        return "processed"

    def _attempt_row(self, session: Session, job: Mapping[str, Any], message: Message, envelope: Envelope | None,
                     attempt: int, outcome: str, reason: str | None, now: datetime) -> None:
        session.execute(insert(job_attempts).values(
            job_key=job["job_key"], attempt=attempt, attempt_epoch=job["attempt_epoch"], topic=message.topic,
            partition=message.partition, offset=message.offset, consumer_group=self.group,
            container=self.container[:120], trace_id=envelope.trace_id if envelope else None, started_at=now,
            finished_at=now, outcome=outcome, reason_code=reason))

    def _record_failed_attempt(self, message: Message, envelope: Envelope, attempt: int, code: str) -> None:
        if not message.topic.startswith("cmev.cmd."):
            return
        with self.session_factory.begin() as session:
            now = self.clock()
            job = session.execute(select(jobs).where(jobs.c.job_key == envelope.job_key)).mappings().first()
            if job is None or job["state"] == "succeeded":
                return
            session.execute(update(jobs).where(jobs.c.job_key == job["job_key"]).values(
                attempt_count=jobs.c.attempt_count + 1, reason_code=code,
                last_error={"reason_code": code, "attempt": attempt}, updated_at=now))
            self._attempt_row(session, job, message, envelope, attempt, "failed", code, now)

    # ------------------------------------------------------------------ dead letters
    def _dead_letter(self, message: Message, envelope: Envelope | None, reason_code: str, dlq_reason: str,
                     history: list[dict[str, Any]]) -> str:
        salvaged = None if envelope else _salvage(message.value)
        with self.session_factory.begin() as session:
            now = self.clock()
            dedup = envelope.dedup_key if envelope else hashlib.sha256(
                json.dumps([message.topic, message.partition, message.offset, _jsonable(message.value)],
                           sort_keys=True).encode()).hexdigest()
            if salvaged is not None:
                salvaged["dedup_key"] = dedup
            if dedup:
                if session.execute(select(consumed_messages.c.id).where(
                        consumed_messages.c.consumer_group == self.group,
                        consumed_messages.c.dedup_key == dedup)).first():
                    self.counters["duplicate"] += 1
                    return "duplicate"
                session.execute(insert(consumed_messages).values(
                    dedup_key=dedup, consumer_group=self.group, topic=message.topic,
                    job_key=envelope.job_key if envelope else salvaged["job_key"] if salvaged else None,
                    received_at=now, completed_at=now, outcome="dead_lettered"))
            ident = ({"claim_id": envelope.claim_id, "input_revision": envelope.input_revision,
                      "job_key": envelope.job_key, "trace_id": envelope.trace_id,
                      "versions": dict(envelope.versions)} if envelope else salvaged)
            job = None
            if ident:
                job = session.execute(select(jobs).where(jobs.c.job_key == ident["job_key"])
                                      .with_for_update()).mappings().first()
            superseded = bool(ident and self.is_superseded and
                              self.is_superseded(session, ident["claim_id"], ident["input_revision"]))
            is_command = message.topic.startswith("cmev.cmd.")
            replayable = bool(is_command and job is not None and dlq_reason != "envelope_invalid" and not superseded)
            if is_command and job is not None and job["state"] != "succeeded":
                session.execute(update(jobs).where(jobs.c.job_key == job["job_key"]).values(
                    state="dead_lettered", reason_code=reason_code, updated_at=now,
                    last_error={"reason_code": reason_code, "dlq_reason": dlq_reason, "attempts": len(history)}))
                self._attempt_row(session, job, message, envelope, len(history), "dead_lettered", reason_code, now)
                if job["task"] in STAGE_TASKS and envelope is not None:
                    self._emit_job_failed(session, job, envelope, reason_code, history, now)
            if ident and self.on_dead_letter and not is_command:
                self.on_dead_letter(session, ident, message.topic, reason_code, now)
            published = False
            if ident:
                enqueue(session, DLQ_TOPIC, self._dlq_message(message, ident, envelope, dlq_reason, history,
                                                              replayable, now), now=now)
                published = True
            original = message.value if isinstance(message.value, Mapping) else {"raw": str(message.value)[:4096]}
            session.execute(insert(dead_letters).values(
                topic=message.topic, consumer_group=self.group, dedup_key=dedup,
                job_key=ident["job_key"] if ident else None, claim_id=ident["claim_id"] if ident else None,
                input_revision=ident["input_revision"] if ident else None, reason_code=reason_code,
                dlq_reason=dlq_reason, reason_text=history[-1]["reason_text"], message=_jsonable(original),
                failure_history=_jsonable(history), replayable=replayable, dlq_published=published, created_at=now))
        self.counters["dead_lettered"] += 1
        log.error("%s dead-lettered a %s message: %s (%s)", self.group, message.topic, reason_code, dlq_reason)
        return "dead_lettered"

    def _emit_job_failed(self, session: Session, job: Mapping[str, Any], envelope: Envelope, reason_code: str,
                         history: list[dict[str, Any]], now: datetime) -> None:
        payload = {"stage": job["task"], "target_ref": job["target"] if job["target"] != "all" else None,
                   "reason_code": reason_code, "reason_text": history[-1]["reason_text"],
                   "attempt": max(1, len(history)), "retryable": False}
        message = build_message(JOB_FAILED_TOPIC, claim_id=job["claim_id"], input_revision=job["input_revision"],
                                task=job["task"], versions=job["versions"], target=job["target"],
                                attempt_epoch=job["attempt_epoch"], trace_id=envelope.trace_id, occurred_at=now,
                                provenance={"source_kind": self.source_kind, "runtime_profile": self.profile,
                                            "producer_service": self.service},
                                payload=payload, causation_id=envelope.dedup_key)
        if message["job_key"] != job["job_key"]:
            raise RuntimeError("job row versions no longer reproduce its job key")
        enqueue(session, JOB_FAILED_TOPIC, message, now=now)

    def _dlq_message(self, message: Message, ident: Mapping[str, Any], envelope: Envelope | None, dlq_reason: str,
                     history: list[dict[str, Any]], replayable: bool, now: datetime) -> dict[str, Any]:
        original_dedup = envelope.dedup_key if envelope else ident.get("dedup_key") or "none"
        # Rejected bytes remain in ops.dead_letters. Republishing them inside a
        # validated message can repeat the original rejection (or exceed its limit).
        reference = {"store": "ops.dead_letters", "consumer_group": self.group,
                     "topic": message.topic, "partition": message.partition, "offset": message.offset,
                     "sha256": hashlib.sha256(json.dumps(_jsonable(message.value), sort_keys=True).encode()).hexdigest()}
        original_envelope = {"record_ref": reference}
        original_payload = {"record_ref": reference}
        history = [{**h, "reason_text": h["reason_code"]} for h in history]
        dlq_envelope = Envelope(
            claim_id=ident["claim_id"], input_revision=ident["input_revision"], job_key=ident["job_key"],
            versions=ident["versions"], trace_id=ident["trace_id"], occurred_at=now,
            provenance={"source_kind": self.source_kind, "runtime_profile": self.profile,
                        "producer_service": self.service},
            dedup_key=hashlib.sha256(f"{DLQ_TOPIC}|{self.group}|{original_dedup}".encode()).hexdigest(),
            causation_id=original_dedup if re.match(r"^[0-9a-f]{64}$", original_dedup) else None)
        stamps = [h["occurred_at"] for h in history]
        payload = {"original_topic": message.topic, "original_envelope": _jsonable(original_envelope),
                   "original_payload": _jsonable(original_payload),
                   "failure_history": _jsonable([{**h, "attempt": max(1, h["attempt"])} for h in history]),
                   "dlq_reason": dlq_reason, "first_failed_at": _iso(min(stamps)), "last_failed_at": _iso(max(stamps)),
                   "replayable": replayable}
        return validate_message(DLQ_TOPIC, dlq_envelope.message(payload))


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda v: _iso(v) if isinstance(v, datetime) else str(v)))


def consume_batch(runtime: ConsumerRuntime, consumer: Consumer, max_records: int = 50) -> int:
    """Poll one batch, process every message, then commit offsets for the whole batch.

    An infrastructure exception propagates before ``commit``: nothing in the batch is
    acknowledged, and redelivery is absorbed by step 3.
    """
    batch = consumer.poll(max_records)
    for message in batch:
        runtime.process(message)
    if batch:
        consumer.commit()
    return len(batch)


def drain(runtimes: Iterable[tuple[ConsumerRuntime, Consumer]]) -> int:
    return sum(consume_batch(runtime, consumer) for runtime, consumer in runtimes)


__all__ = ["Context", "ConsumerRuntime", "Handler", "JOB_FAILED_TOPIC", "PermanentError", "RetryPolicy",
           "TransientError", "consume_batch", "drain"]
