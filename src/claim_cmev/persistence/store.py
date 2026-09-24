"""Insert-only store for validated contract records produced by the branch stages.

Every stage result is written as one ``pipeline.stage_records`` row per record, keyed by
the record's own deterministic ID and tied to the producing job key, in the producing
job's transaction. A replay never reaches this code (the job is already ``succeeded``);
a second record with an existing ID is an ``IntegrityError`` (a conflict), never an
overwrite. Rows are re-validated into contract models on load.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ..contracts.common import deterministic_id
from ..contracts.documents import DeclarationCompleteness, DocumentPage, LineItem, PenMark
from ..contracts.imaging import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    ImageQuality,
    PartCoverage,
    PartPrediction,
    PartSummary,
)
from .tables import stage_records

RECORD_KINDS: dict[str, type[BaseModel] | None] = {
    "image_quality": ImageQuality,
    "part_prediction": PartPrediction,
    "damage_observation": ImageDamageObservation,
    "part_summary": PartSummary,
    "part_coverage": PartCoverage,
    "identity_confirmation": IdentityConfirmation,
    "coverage_confirmation": CoverageConfirmation,
    "document_page": DocumentPage,
    "line_item": LineItem,
    "declaration_completeness": DeclarationCompleteness,
    "pen_mark": PenMark,
    "fixture_mark_action": None,  # a fixture surveyor's pen-mark action (plain mapping, see fixtures.py)
}
_ID_FIELDS = {"part_prediction": "prediction_id", "damage_observation": "observation_id", "part_summary": "summary_id",
              "part_coverage": "coverage_id", "identity_confirmation": "confirmation_id",
              "coverage_confirmation": "confirmation_id", "document_page": "page_id", "line_item": "entry_id",
              "pen_mark": "mark_id", "fixture_mark_action": "action_id"}


def record_id(kind: str, record: Any, job_key: str) -> str:
    """The record's own ID, or a deterministic one for records that have none."""
    body = record if isinstance(record, dict) else record.__dict__
    field = _ID_FIELDS.get(kind)
    if field:
        return body[field]
    if kind == "image_quality":
        return deterministic_id("iq", job_key, body["photo_id"])
    if kind == "declaration_completeness":
        return deterministic_id("dc", job_key)
    raise KeyError(kind)


def insert_records(session: Session, records: Iterable[tuple[str, Any]], *, claim_id: str, input_revision: int,
                   stage: str, job_key: str, now: datetime) -> list[str]:
    ids = []
    for kind, record in records:
        if kind not in RECORD_KINDS:
            raise KeyError(f"unknown record kind {kind!r}")
        body = record if isinstance(record, dict) else record.model_dump(mode="json")
        rid = record_id(kind, record, job_key)
        session.execute(insert(stage_records).values(
            record_id=rid, record_kind=kind, claim_id=claim_id, input_revision=input_revision, stage=stage,
            job_key=job_key, body=body, created_at=now))
        ids.append(rid)
    return ids


def load_records(session: Session, claim_id: str, job_keys: Sequence[str]) -> dict[str, list[Any]]:
    """Records produced by ``job_keys``, validated into contract models, grouped by kind in insert order."""
    grouped: dict[str, list[Any]] = defaultdict(list)
    if not job_keys:
        return grouped
    rows = session.execute(select(stage_records.c.record_kind, stage_records.c.body)
                           .where(stage_records.c.claim_id == claim_id, stage_records.c.job_key.in_(list(job_keys)))
                           .order_by(stage_records.c.id)).all()
    for kind, body in rows:
        model = RECORD_KINDS[kind]
        grouped[kind].append(model.model_validate(body) if model else dict(body))
    return grouped


__all__ = ["RECORD_KINDS", "insert_records", "load_records", "record_id"]
