"""``cmev-orchestrator``: fan-out, stage chaining, the branch join and the single consolidate emit.

The orchestrator computes nothing about a claim's content. It reads job rows, their
result references and the branch ledger, and writes commands into the outbox in the
same transaction that records its decision. The join is evaluated from the tables on
every event, so branch completions may arrive in either order; the single-emit claim on
``ops.branch_state.consolidate_emitted_at`` makes ``cmd.consolidate`` go out once even
with several replicas.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..messaging.consumer import Context, PermanentError
from ..persistence.tables import branch_state, consumed_messages, jobs
from . import state
from .plan import (
    EVENT_TOPIC,
    INPUT_TOPIC,
    SERVICE,
    STAGE_OF_EVENT,
    STAGE_OF_TASK,
    STAGES,
    VersionBundle,
    consolidate_versions,
    page_target,
    pinned_versions,
)

GROUP = "cmev-orchestrator"


def _split(value: str | None) -> tuple[str, str]:
    """``"name/version"`` into its parts; a bare value is its own id and version."""
    if not value:
        return "not-recorded", "0"
    name, _, version = value.partition("/")
    return name, version or value


class Orchestrator:
    def __init__(self, versions: VersionBundle, rules_config_version: str):
        self.versions, self.rules_config_version = versions, rules_config_version

    def handlers(self) -> dict[str, Any]:
        routes: dict[str, Any] = {INPUT_TOPIC: self.on_input_revision,
                                  "cmev.evt.job-failed.v1": self.on_job_failed}
        for topic in EVENT_TOPIC.values():
            routes[topic] = self.on_stage_completed
        return routes

    # ------------------------------------------------------------------ handlers
    def on_input_revision(self, ctx: Context) -> dict[str, Any]:
        env, payload, session = ctx.envelope, ctx.payload, ctx.session
        bs = state.branch_row(session, env.claim_id, env.input_revision, lock=True)
        if bs is None:
            raise PermanentError("branch_state_missing", "the API did not record a branch ledger for this revision")
        pages = [(page_target(p["file_id"], p["page_number"] or 1, p["media_type"] == "application/pdf"), p)
                 for p in payload["pages"]]
        items = {"photo": {(p["file_id"], p["sha256"]) for p in payload["photos"]},
                 "page": {(target, p["sha256"]) for target, p in pages}}
        stage_versions = {s: self.versions.for_stage(s) for s in STAGES}
        outcomes = state.apply_reuse(session, bs, previous_revision=payload["previous_input_revision"],
                                     reused=payload.get("reused_artifacts") or [], current_items=items,
                                     stage_versions=stage_versions, now=ctx.now)
        intake = {"currency": payload["currency"], "photo_ids": [p["file_id"] for p in payload["photos"]],
                  "page_targets": [t for t, _ in pages], "reuse": outcomes}
        session.execute(update(jobs).where(jobs.c.job_key == env.job_key, jobs.c.state != "succeeded")
                        .values(state="succeeded", result_ref=intake, updated_at=ctx.now))
        self._advance(ctx, photos=payload["photos"], pages=pages)
        return intake

    def on_stage_completed(self, ctx: Context) -> dict[str, Any]:
        env, session = ctx.envelope, ctx.session
        stage = STAGE_OF_EVENT[ctx.topic]
        if state.branch_row(session, env.claim_id, env.input_revision, lock=True) is None:
            raise PermanentError("branch_state_missing", "completion event for an unknown input revision")
        job = state.job_row(session, env.job_key)
        if job is None or job["state"] != "succeeded":
            raise PermanentError("job_state_mismatch", "a completion event must follow a succeeded job")
        self._advance(ctx)
        return {"stage": stage, "job_key": env.job_key}

    def on_job_failed(self, ctx: Context) -> dict[str, Any]:
        env, session = ctx.envelope, ctx.session
        bs = state.branch_row(session, env.claim_id, env.input_revision, lock=True)
        job = state.job_row(session, env.job_key)
        if bs is None or job is None or job["state"] not in ("failed", "dead_lettered"):
            return {"ignored": "stale_failure"}  # already retried, or unknown revision
        stage = STAGE_OF_TASK[ctx.payload["stage"]]
        state.record_failure(session, bs, stage=stage, job_key_value=env.job_key,
                             reason_code=ctx.payload["reason_code"], now=ctx.now)
        return {"stage": stage, "reason_code": ctx.payload["reason_code"]}

    def on_dead_letter(self, session: Session, ident: Mapping[str, Any], topic: str, reason_code: str,
                       now: datetime) -> None:
        """This orchestrator could not process an event: the affected stages fail visibly."""
        claim_id, revision, key = ident["claim_id"], ident["input_revision"], ident["job_key"]
        bs = state.branch_row(session, claim_id, revision, lock=True)
        if bs is None:
            return
        handled = session.execute(select(consumed_messages.c.id).where(
            consumed_messages.c.consumer_group == GROUP, consumed_messages.c.topic == topic,
            consumed_messages.c.job_key == key, consumed_messages.c.outcome == "processed")).first()
        if handled:
            return  # a valid delivery of this job's event already did the work; a bad copy fails nothing
        if topic == INPUT_TOPIC:
            session.execute(update(jobs).where(jobs.c.job_key == key, jobs.c.state != "succeeded")
                            .values(state="dead_lettered", reason_code=reason_code, updated_at=now))
            for stage in STAGES:
                if bs[stage] not in ("done", "not_required"):
                    state.record_failure(session, bs, stage=stage, job_key_value=key, reason_code=reason_code,
                                         now=now)
                    bs = state.branch_row(session, claim_id, revision, lock=True)
        elif topic in STAGE_OF_EVENT:
            state.record_failure(session, bs, stage=STAGE_OF_EVENT[topic], job_key_value=key,
                                 reason_code=reason_code, now=now)

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, ctx: Context, stage: str, payload: Mapping[str, Any], target: str = "all") -> str:
        env = ctx.envelope
        return state.dispatch(ctx.session, claim_id=env.claim_id, input_revision=env.input_revision, stage=stage,
                              versions=self.versions.for_stage(stage), payload=payload, trace_id=env.trace_id,
                              provenance=ctx.provenance(), now=ctx.now, target=target,
                              causation_id=env.dedup_key)

    def _advance(self, ctx: Context, photos: list[Mapping[str, Any]] | None = None,
                 pages: list[tuple[str, Mapping[str, Any]]] | None = None) -> None:
        session, env = ctx.session, ctx.envelope
        claim, rev = env.claim_id, env.input_revision
        bs = state.refresh_all(session, claim, rev, ctx.now)
        dispatched = False
        if bs["parts"] == "pending" and photos:
            for photo in photos:
                self._dispatch(ctx, "parts", self._parts_payload(photo), target=photo["file_id"])
            dispatched = True
        if bs["damage"] in ("pending", "running"):
            existing = {j["target"] for j in state.stage_jobs(session, claim, rev, "damage")}
            for parts_job in state.effective_jobs(session, claim, rev, "parts"):
                if parts_job["target"] not in existing:
                    self._dispatch(ctx, "damage", self._damage_payload(parts_job), target=parts_job["target"])
                    dispatched = True
        if bs["page_read"] == "pending" and pages:
            for target, page in pages:
                self._dispatch(ctx, "page_read", self._page_payload(page), target=target)
            dispatched = True
        if dispatched:
            bs = state.refresh_all(session, claim, rev, ctx.now)
        if bs["summary"] == "pending" and bs["damage"] == "done":
            self._dispatch(ctx, "summary", self._summary_payload(session, claim, rev))
        if bs["line_items"] == "pending" and bs["page_read"] == "done":
            self._dispatch(ctx, "line_items", self._line_items_payload(session, claim, rev))
        if bs["pen_marks"] == "pending" and bs["line_items"] == "done":
            self._dispatch(ctx, "pen_marks", self._pen_marks_payload(session, claim, rev))
        bs = state.refresh_all(session, claim, rev, ctx.now)
        if state.join_ready(bs) and bs["consolidate_emitted_at"] is None:
            if state.claim_consolidate_emit(session, bs["id"], ctx.now):
                dispatch_consolidate(session, bs, rules_config_version=self.rules_config_version,
                                     provenance=ctx.provenance(), now=ctx.now, causation_id=env.dedup_key)

    # ------------------------------------------------------------------ command payloads
    def _parts_payload(self, photo: Mapping[str, Any]) -> dict[str, Any]:
        v = self.versions.for_stage("parts")
        model_id, model_version = _split(v.get("parts_model"))
        return {"photo": dict(photo), "model_id": model_id, "model_version": model_version,
                "preprocess_config_version": v.get("preprocess_config", "not-recorded"),
                "taxonomy_version": v.get("taxonomy", "not-recorded")}

    def _damage_payload(self, parts_job: Mapping[str, Any]) -> dict[str, Any]:
        v = self.versions.for_stage("damage")
        model_id, model_version = _split(v.get("damage_model"))
        result = parts_job["result_ref"] or {}
        return {"photo": parts_job["command"]["payload"]["photo"], "part_mask_ref": result["part_mask_ref"],
                "transform": result["transform"], "model_id": model_id, "model_version": model_version,
                "preprocess_config_version": self.versions.for_stage("parts").get("preprocess_config", "not-recorded"),
                "assignment_config_version": v.get("assignment_config", "not-recorded"),
                "taxonomy_version": v.get("taxonomy", "not-recorded")}

    def _summary_payload(self, session: Session, claim: str, rev: int) -> dict[str, Any]:
        v = self.versions.for_stage("summary")
        parts = state.effective_jobs(session, claim, rev, "parts")
        damage = state.effective_jobs(session, claim, rev, "damage")
        return {"observation_ids": [i for j in damage for i in (j["result_ref"] or {}).get("observation_ids", [])],
                "part_prediction_ids": [i for j in parts for i in (j["result_ref"] or {}).get("part_prediction_ids", [])],
                "photo_ids": [j["target"] for j in parts], "identity_confirmations": [], "coverage_confirmations": [],
                "summary_config_version": v.get("summary_config", "not-recorded"),
                "taxonomy_version": v.get("taxonomy", "not-recorded")}

    def _page_payload(self, page: Mapping[str, Any]) -> dict[str, Any]:
        v = self.versions.for_stage("page_read")
        engine, version = _split(v.get("ocr"))
        return {"page": dict(page), "ocr_engine": engine, "ocr_version": version,
                "preprocess_config_version": v.get("preprocess_config", "not-recorded")}

    def _pages(self, session: Session, claim: str, rev: int, reading: bool) -> list[dict[str, Any]]:
        found = []
        for job in state.effective_jobs(session, claim, rev, "page_read"):
            r = job["result_ref"] or {}
            page = {"page_id": r["page_id"], "page_number": r["page_number"],
                    "corrected_render_ref": r["corrected_render_ref"], "transform": r["transform"]}
            if reading:
                page["page_reading_ref"] = r["page_reading_ref"]
            found.append(page)
        return sorted(found, key=lambda p: (p["page_number"], p["page_id"]))

    def _currency(self, session: Session, claim: str, rev: int) -> str:
        intake = [j for j in state.stage_jobs(session, claim, rev, "intake") if j["state"] == "succeeded"]
        return ((intake[0]["result_ref"] or {}).get("currency") if intake else None) or "SGD"

    def _line_items_payload(self, session: Session, claim: str, rev: int) -> dict[str, Any]:
        v = self.versions.for_stage("line_items")
        return {"pages": self._pages(session, claim, rev, True), "parser_config_version": v.get("parser_config", "not-recorded"),
                "taxonomy_version": v.get("taxonomy", "not-recorded"), "claim_currency": self._currency(session, claim, rev),
                "engine": "parser", "model_id": None, "model_version": None}

    def _pen_marks_payload(self, session: Session, claim: str, rev: int) -> dict[str, Any]:
        v = self.versions.for_stage("pen_marks")
        model_id, model_version = _split(v.get("penmark_model"))
        rows = [row for j in state.effective_jobs(session, claim, rev, "line_items")
                for row in (j["result_ref"] or {}).get("row_boxes", [])]
        return {"pages": self._pages(session, claim, rev, False), "row_boxes": rows, "model_id": model_id,
                "model_version": model_version, "link_config_version": v.get("link_config", "not-recorded"),
                "trocr_enabled": False}


def dispatch_consolidate(session: Session, bs: Mapping[str, Any], *, rules_config_version: str,
                         provenance: Mapping[str, Any], now: datetime, causation_id: str | None = None,
                         trigger: str | None = None, cost_table_version: str | None = None) -> str:
    """Write the consolidate job and command for one input revision (after the emit claim, or explicitly)."""
    claim, rev = bs["claim_id"], bs["input_revision"]
    effective = {s: state.effective_jobs(session, claim, rev, s) for s in STAGES}

    def ids(stage: str, field: str) -> list[str]:
        return [i for j in effective[stage] for i in (j["result_ref"] or {}).get(field, [])]

    branches = state.branch_states(bs)
    table = cost_table_version or bs["cost_table_version"]
    payload = {"trigger": trigger or bs["trigger"], "image_branch_state": branches["image"],
               "document_branch_state": branches["document"], "summary_ids": ids("summary", "summary_ids"),
               "coverage_ids": ids("summary", "coverage_ids"), "line_item_ids": ids("line_items", "line_item_ids"),
               "mark_ids": ids("pen_marks", "mark_ids"), "review_revision": bs["review_revision"] or None,
               "cost_table_version": table, "rules_config_version": rules_config_version,
               "pinned_versions": pinned_versions({s: (rows[0]["versions"] if rows else {})
                                                   for s, rows in effective.items()}) or {"code": "0.2.0"},
               "reason": None}
    key = state.dispatch(session, claim_id=claim, input_revision=rev, stage="consolidate",
                         versions=consolidate_versions(rules_config_version, table), payload=payload,
                         trace_id=bs["trace_id"], provenance={**provenance, "producer_service": SERVICE["orchestrator"]},
                         now=now, causation_id=causation_id)
    session.execute(update(branch_state).where(branch_state.c.id == bs["id"])
                    .values(consolidate_job_key=key, updated_at=now))
    return key


__all__ = ["GROUP", "Orchestrator", "dispatch_consolidate"]
