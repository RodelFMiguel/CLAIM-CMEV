"""Job state machine, branch ledger, join policy, reuse lineage and operator retry.

Shared by ``cmev-api`` (commit, processing view, retry) and ``cmev-orchestrator``. State
lives in PostgreSQL, never in a Kafka offset: the join is read from ``ops.branch_state``
and ``ops.jobs``, so completion events may arrive in any order.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from ..contracts.claims import ReusedArtifact
from ..messaging.outbox import build_message, enqueue
from ..persistence.tables import branch_state, dead_letters, jobs, reuse_lineage
from .plan import (
    BRANCH_OF,
    COMMAND_TOPIC,
    DOCUMENT_STAGES,
    IMAGE_STAGES,
    PER_ITEM,
    STAGE_OF_TASK,
    STAGES,
    TASKS,
    job_key,
)

JOB_TRANSITIONS = {
    "pending": {"dispatched", "running", "dead_lettered", "failed"},
    "dispatched": {"running", "dead_lettered", "failed"},
    "running": {"succeeded", "failed", "dead_lettered"},
    "failed": {"pending"},
    "dead_lettered": {"pending"},
    "succeeded": set(),
    "not_required": set(),
}
TERMINAL_STAGE_STATES = frozenset({"done", "not_required"})


class JobConflict(RuntimeError):
    """HTTP 409: the job already succeeded and is never re-dispatched."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class JobNotRetryable(RuntimeError):
    """HTTP 422: the job is not in a retryable state, or its revision is superseded."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def can_transition(before: str, after: str) -> bool:
    return after in JOB_TRANSITIONS.get(before, set())


# ------------------------------------------------------------------ jobs
def dispatch(session: Session, *, claim_id: str, input_revision: int, stage: str, versions: Mapping[str, str],
             payload: Mapping[str, Any], trace_id: str, provenance: Mapping[str, Any], now: datetime,
             target: str = "all", causation_id: str | None = None, assessment_revision: int | None = None) -> str:
    """Create a ``pending`` job row and write its command into the outbox, in the caller's transaction.

    Idempotent on the job key: an existing job is returned untouched, never re-dispatched.
    """
    key = job_key(claim_id, input_revision, stage, versions, target)
    if session.execute(select(jobs.c.id).where(jobs.c.job_key == key)).first():
        return key
    topic = COMMAND_TOPIC[stage]
    optional = {"causation_id": causation_id} if causation_id else {}
    if assessment_revision is not None:
        optional["assessment_revision"] = assessment_revision
    message = build_message(topic, claim_id=claim_id, input_revision=input_revision, task=TASKS[stage],
                            versions=versions, provenance=provenance, trace_id=trace_id, occurred_at=now,
                            payload=payload, target=target, **optional)
    session.execute(insert(jobs).values(
        job_key=key, claim_id=claim_id, input_revision=input_revision, task=TASKS[stage], target=target,
        version_signature=key.rsplit(":", 1)[1], stage=stage, state="pending", attempt_count=0, attempt_epoch=0,
        versions=dict(versions), command={"topic": topic, "payload": dict(payload), "trace_id": trace_id,
                                          "provenance": dict(provenance), "causation_id": causation_id,
                                          "assessment_revision": assessment_revision},
        created_at=now, updated_at=now))
    enqueue(session, topic, message, now=now, job_key=key)
    return key


def create_intake_job(session: Session, *, claim_id: str, input_revision: int, versions: Mapping[str, str],
                      message: Mapping[str, Any], now: datetime) -> str:
    """The API's ``intake`` job: pending until the orchestrator has fanned the revision out."""
    key = message["job_key"]
    session.execute(insert(jobs).values(
        job_key=key, claim_id=claim_id, input_revision=input_revision, task="intake", target="all",
        version_signature=key.rsplit(":", 1)[1], stage="intake", state="pending", attempt_count=0, attempt_epoch=0,
        versions=dict(versions), command={"topic": "cmev.evt.input-revision-created.v1",
                                          "trace_id": message["trace_id"]},
        created_at=now, updated_at=now))
    enqueue(session, "cmev.evt.input-revision-created.v1", message, now=now, job_key=key)
    return key


def job_row(session: Session, key: str, *, lock: bool = False) -> Mapping[str, Any] | None:
    query = select(jobs).where(jobs.c.job_key == key)
    return session.execute(query.with_for_update() if lock else query).mappings().first()


def revision_jobs(session: Session, claim_id: str, input_revision: int | None = None) -> list[Mapping[str, Any]]:
    query = select(jobs).where(jobs.c.claim_id == claim_id)
    if input_revision is not None:
        query = query.where(jobs.c.input_revision == input_revision)
    return list(session.execute(query.order_by(jobs.c.id)).mappings())


def stage_jobs(session: Session, claim_id: str, input_revision: int, stage: str) -> list[Mapping[str, Any]]:
    return list(session.execute(select(jobs).where(
        jobs.c.claim_id == claim_id, jobs.c.input_revision == input_revision, jobs.c.task == TASKS[stage])
        .order_by(jobs.c.id)).mappings())


# ------------------------------------------------------------------ branch ledger
def create_branch_state(session: Session, *, claim_id: str, input_revision: int, photo_count: int, page_count: int,
                        trace_id: str, cost_table_version: str, now: datetime, review_revision: int | None = None,
                        trigger: str = "branches_complete") -> None:
    stages = {s: ("pending" if photo_count else "not_required") for s in IMAGE_STAGES}
    stages.update({s: ("pending" if page_count else "not_required") for s in DOCUMENT_STAGES})
    reasons = {s: ["no_photographs"] for s in IMAGE_STAGES if not photo_count}
    reasons.update({s: ["no_estimate_pages"] for s in DOCUMENT_STAGES if not page_count})
    session.execute(insert(branch_state).values(
        claim_id=claim_id, input_revision=input_revision, expected_photos=photo_count, expected_pages=page_count,
        **stages, reasons=reasons, failures=[], trace_id=trace_id, cost_table_version=cost_table_version,
        review_revision=review_revision, trigger=trigger, created_at=now, updated_at=now))


def branch_row(session: Session, claim_id: str, input_revision: int, *, lock: bool = False) -> Mapping[str, Any] | None:
    query = select(branch_state).where(branch_state.c.claim_id == claim_id,
                                       branch_state.c.input_revision == input_revision)
    return session.execute(query.with_for_update() if lock else query).mappings().first()


def latest_revision(session: Session, claim_id: str) -> int | None:
    return session.execute(select(func.max(branch_state.c.input_revision))
                           .where(branch_state.c.claim_id == claim_id)).scalar()


def is_superseded(session: Session, claim_id: str, input_revision: int) -> bool:
    """A newer input revision exists for the claim."""
    latest = latest_revision(session, claim_id)
    return latest is not None and latest > input_revision


def lineage_row(session: Session, claim_id: str, input_revision: int, stage: str) -> Mapping[str, Any] | None:
    return session.execute(select(reuse_lineage).where(
        reuse_lineage.c.claim_id == claim_id, reuse_lineage.c.input_revision == input_revision,
        reuse_lineage.c.stage == stage)).mappings().first()


def effective_jobs(session: Session, claim_id: str, input_revision: int, stage: str) -> list[Mapping[str, Any]]:
    """The succeeded jobs whose results stand for ``stage`` of this revision, following reuse lineage."""
    lineage = lineage_row(session, claim_id, input_revision, stage)
    if lineage:
        keys = list(lineage["source_job_keys"])
        rows = {r["job_key"]: r for r in session.execute(select(jobs).where(jobs.c.job_key.in_(keys))).mappings()}
        return [rows[k] for k in keys if k in rows]
    return [j for j in stage_jobs(session, claim_id, input_revision, stage) if j["state"] == "succeeded"]


def expected_items(bs: Mapping[str, Any], stage: str) -> int:
    kind = PER_ITEM.get(stage)
    return bs["expected_photos"] if kind == "photo" else bs["expected_pages"] if kind == "page" else 1


def refresh_stage(session: Session, bs: Mapping[str, Any], stage: str, now: datetime) -> str:
    """Recompute one stage's state from its jobs (or its reuse lineage) and store it."""
    current = bs[stage]
    if current == "not_required":
        return current
    if lineage_row(session, bs["claim_id"], bs["input_revision"], stage):
        state = "done"
    else:
        rows = stage_jobs(session, bs["claim_id"], bs["input_revision"], stage)
        if any(r["state"] in ("failed", "dead_lettered") for r in rows):
            state = "failed"
        elif len({r["target"] for r in rows if r["state"] == "succeeded"}) >= expected_items(bs, stage) and rows:
            state = "done"
        else:
            state = "running" if rows else "pending"
    if state != current:
        session.execute(update(branch_state).where(branch_state.c.id == bs["id"])
                        .values({stage: state, "updated_at": now}))
    return state


def refresh_all(session: Session, claim_id: str, input_revision: int, now: datetime) -> Mapping[str, Any]:
    bs = branch_row(session, claim_id, input_revision, lock=True)
    for stage in STAGES:
        refresh_stage(session, bs, stage, now)
    return branch_row(session, claim_id, input_revision)


def branch_states(bs: Mapping[str, Any]) -> dict[str, str]:
    """Per-branch summary: ``complete``, ``failed``, ``skipped_no_photos``/``skipped_no_pages``, ``running`` or ``pending``."""
    result = {}
    for branch, stages, skipped in (("image", IMAGE_STAGES, "skipped_no_photos"),
                                    ("document", DOCUMENT_STAGES, "skipped_no_pages")):
        states = [bs[s] for s in stages]
        if all(s == "not_required" for s in states):
            result[branch] = skipped
        elif any(s == "failed" for s in states):
            result[branch] = "failed"
        elif all(s in TERMINAL_STAGE_STATES for s in states):
            result[branch] = "complete"
        elif any(s in ("running", "done") for s in states):
            result[branch] = "running"
        else:
            result[branch] = "pending"
    return result


def join_ready(bs: Mapping[str, Any]) -> bool:
    """Every stage done or not required, none failed, and at least one branch has input."""
    states = [bs[s] for s in STAGES]
    return all(s in TERMINAL_STAGE_STATES for s in states) and any(s == "done" for s in states)


def claim_consolidate_emit(session: Session, bs_id: int, now: datetime) -> bool:
    """The single-emit claim: only one transaction gets a row back (application platform 6.3)."""
    result = session.execute(update(branch_state).where(
        branch_state.c.id == bs_id, branch_state.c.consolidate_emitted_at.is_(None))
        .values(consolidate_emitted_at=now, updated_at=now))
    return result.rowcount == 1


def record_failure(session: Session, bs: Mapping[str, Any], *, stage: str, job_key_value: str, reason_code: str,
                   now: datetime) -> None:
    failures = [f for f in bs["failures"] if f.get("job_key") != job_key_value]
    failures.append({"stage": stage, "job_key": job_key_value, "reason_code": reason_code})
    values: dict[str, Any] = {"failures": failures, "updated_at": now}
    if stage in STAGES:
        values[stage] = "failed"
        reasons = dict(bs["reasons"])
        reasons[stage] = sorted(set(reasons.get(stage, [])) | {reason_code})
        values["reasons"] = reasons
    session.execute(update(branch_state).where(branch_state.c.id == bs["id"]).values(values))


# ------------------------------------------------------------------ reuse lineage
def reuse_artifacts(session: Session, claim_id: str, input_revision: int,
                    stages: Sequence[str] = STAGES) -> list[ReusedArtifact]:
    """What a new revision may reuse from ``input_revision``: one entry per effective stage job."""
    found = []
    for stage in stages:
        for row in effective_jobs(session, claim_id, input_revision, stage):
            found.append(ReusedArtifact(artifact_id=row["job_key"], kind=f"{stage}_records",
                                        source_input_revision=row["input_revision"], producing_job_key=row["job_key"]))
    return found


def _item_fingerprint(row: Mapping[str, Any]) -> tuple[str, str] | None:
    payload = (row.get("command") or {}).get("payload") or {}
    ref = payload.get("photo") or payload.get("page")
    return (row["target"], ref["sha256"]) if ref else None


def apply_reuse(session: Session, bs: Mapping[str, Any], *, previous_revision: int | None,
                reused: Sequence[Mapping[str, Any]], current_items: Mapping[str, set[tuple[str, str]]],
                stage_versions: Mapping[str, Mapping[str, str]], now: datetime) -> dict[str, str]:
    """Validate a ``reused_artifacts`` hint and record lineage for each reusable stage.

    A stage is reused only when (a) the hint lists exactly the previous revision's
    effective jobs for it, (b) every one of those jobs ran with the versions this stage
    would run with now, (c) its per-item inputs are byte-identical (same targets and
    SHA-256), and (d) every upstream stage of its branch is reused too. Anything else
    is refused with a reason and the stage is dispatched. Returns ``stage -> outcome``.
    """
    outcomes: dict[str, str] = {}
    if not reused or previous_revision is None:
        return outcomes
    by_stage: dict[str, set[str]] = {}
    for entry in reused:
        stage = entry["kind"].removesuffix("_records")
        by_stage.setdefault(stage, set()).add(entry["producing_job_key"])
    reasons = dict(bs["reasons"])
    for branch_stages in (IMAGE_STAGES, DOCUMENT_STAGES):
        upstream_ok = True
        for stage in branch_stages:
            if stage not in by_stage or bs[stage] == "not_required":
                upstream_ok = False
                continue
            source = effective_jobs(session, bs["claim_id"], previous_revision, stage)
            refusal = None
            if not upstream_ok:
                refusal = "reuse_refused_upstream_rerun"
            elif not source or {r["job_key"] for r in source} != by_stage[stage]:
                refusal = "reuse_refused_hint_mismatch"
            elif any(dict(r["versions"]) != dict(stage_versions[stage]) for r in source):
                refusal = "reuse_refused_version_changed"
            elif stage in PER_ITEM and {_item_fingerprint(r) for r in source} != current_items[PER_ITEM[stage]]:
                refusal = "reuse_refused_inputs_changed"
            if refusal:
                upstream_ok = False
                outcomes[stage] = refusal
                reasons[stage] = sorted(set(reasons.get(stage, [])) | {refusal})
                continue
            session.execute(insert(reuse_lineage).values(
                claim_id=bs["claim_id"], input_revision=bs["input_revision"], stage=stage,
                source_input_revision=min(r["input_revision"] for r in source), via_input_revision=previous_revision,
                source_job_keys=[r["job_key"] for r in source], versions=dict(stage_versions[stage]),
                recorded_at=now))
            outcomes[stage] = "reused"
            reasons[stage] = sorted(set(reasons.get(stage, [])) | {"reused_prior_result"})
    session.execute(update(branch_state).where(branch_state.c.id == bs["id"]).values(reasons=reasons, updated_at=now))
    return outcomes


def lineage_records(session: Session, claim_id: str, input_revision: int) -> list[dict[str, Any]]:
    rows = session.execute(select(reuse_lineage).where(
        reuse_lineage.c.claim_id == claim_id, reuse_lineage.c.input_revision == input_revision)
        .order_by(reuse_lineage.c.id)).mappings()
    return [{"stage": r["stage"], "source_input_revision": r["source_input_revision"],
             "via_input_revision": r["via_input_revision"], "source_job_keys": list(r["source_job_keys"]),
             "versions": dict(r["versions"])} for r in rows]


# ------------------------------------------------------------------ operator retry
def retry_job(session: Session, key: str, *, now: datetime) -> Mapping[str, Any]:
    """Re-dispatch a failed or dead-lettered job under a new attempt epoch (new dedup key).

    Never re-dispatches a succeeded job; refuses a superseded input revision. The same
    job key means deterministic result IDs, so a successful retry writes no duplicate.
    """
    job = job_row(session, key, lock=True)
    if job is None:
        raise LookupError(key)
    if job["state"] == "succeeded":
        raise JobConflict("job_succeeded", "A succeeded job is never re-dispatched.")
    if job["state"] not in ("failed", "dead_lettered") or not job["command"] or "payload" not in job["command"]:
        raise JobNotRetryable("job_not_retryable", "Only a failed or dead-lettered stage job can be retried.")
    if is_superseded(session, job["claim_id"], job["input_revision"]):
        raise JobNotRetryable("revision_superseded", "A newer input revision exists; this job is not replayed.")
    epoch = job["attempt_epoch"] + 1
    command = job["command"]
    optional = {k: command[k] for k in ("causation_id", "assessment_revision") if command.get(k) is not None}
    message = build_message(command["topic"], claim_id=job["claim_id"], input_revision=job["input_revision"],
                            task=job["task"], versions=job["versions"], provenance=command["provenance"],
                            trace_id=command["trace_id"], occurred_at=now, payload=command["payload"],
                            target=job["target"], attempt_epoch=epoch, **optional)
    if message["job_key"] != key:
        raise JobNotRetryable("job_key_unreproducible", "The stored command no longer reproduces its job key.")
    session.execute(update(jobs).where(jobs.c.job_key == key).values(
        state="pending", attempt_epoch=epoch, reason_code=None, updated_at=now))
    enqueue(session, command["topic"], message, now=now, job_key=key)
    session.execute(update(dead_letters).where(dead_letters.c.job_key == key, dead_letters.c.replayed_at.is_(None))
                    .values(replayed_at=now))
    bs = branch_row(session, job["claim_id"], job["input_revision"], lock=True)
    if bs is not None:
        failures = [f for f in bs["failures"] if f.get("job_key") != key]
        values: dict[str, Any] = {"failures": failures, "updated_at": now}
        stage = STAGE_OF_TASK.get(job["task"])
        if stage in STAGES and bs[stage] == "failed" and not any(f.get("stage") == stage for f in failures):
            values[stage] = "running"
        session.execute(update(branch_state).where(branch_state.c.id == bs["id"]).values(values))
    return job_row(session, key)


__all__ = [
    "BRANCH_OF", "JOB_TRANSITIONS", "JobConflict", "JobNotRetryable", "apply_reuse", "branch_row", "branch_states",
    "can_transition", "claim_consolidate_emit", "create_branch_state", "create_intake_job", "dispatch",
    "effective_jobs", "expected_items", "is_superseded", "job_row", "join_ready", "latest_revision",
    "lineage_records", "lineage_row", "record_failure", "refresh_all", "refresh_stage", "retry_job",
    "reuse_artifacts", "revision_jobs", "stage_jobs",
]
