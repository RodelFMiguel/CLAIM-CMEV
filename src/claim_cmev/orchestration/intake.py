"""Atomic input-revision commit (application platform section 4), shared by the API and the seeds.

One transaction writes the immutable input record, moves the files to ``committed``,
creates the branch ledger and the ``intake`` job, and puts
``cmev.evt.input-revision-created.v1`` into the outbox. If any part fails, none of it
commits. The cost table new assessments pin is chosen here and never changed afterwards.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import secrets
from typing import Any

from sqlalchemy.orm import Session

from ..contracts.claims import ReusedArtifact
from ..messaging.outbox import build_message
from ..runtime import get, put
from . import state
from .plan import INPUT_TOPIC, SERVICE, STAGES, VersionBundle, page_target

LEGACY_VEHICLE_CLASSES = {"compact-sedan": "sedan_standard", "suv": "suv_crossover", "hatchback": "hatchback_small"}
"""Values the earlier workbench form sent, mapped to the contract classes (recorded decision)."""


def contract_vehicle_class(value: str | None) -> str:
    value = value or "unknown"
    return LEGACY_VEHICLE_CLASSES.get(value, value)


def file_refs(files: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Photo and page ``FileRef``s in the committed order; a PDF contributes one per page."""
    photos, pages = [], []
    for item in files:
        base = {"file_id": item["file_id"], "object_uri": item["object_uri"], "sha256": item["sha256"],
                "media_type": item["media_type"], "byte_count": item["byte_count"],
                "exif_orientation": item.get("exif_orientation")}
        if item["role"] == "photograph":
            photos.append({**base, "width": item["width"], "height": item["height"], "page_number": None})
        else:
            sizes = item.get("page_sizes") or [[item["width"], item["height"]]] * item.get("page_count", 1)
            for number, (width, height) in enumerate(sizes, start=1):
                pages.append({**base, "width": max(1, int(width)), "height": max(1, int(height)),
                              "page_number": number})
    return photos, pages


def commit_input_revision(db: Session, claim: dict[str, Any], files: Sequence[Mapping[str, Any]], *, now: datetime,
                          versions: VersionBundle, cost_table_version: str, profile: str, source_kind: str,
                          corrections: Sequence[Mapping[str, Any]] = (), base_assessment_revision: int | None = None,
                          reuse_from: int | None = None, reuse_stages: Sequence[str] = STAGES,
                          review_revision: int | None = None, trace_id: str | None = None) -> dict[str, Any]:
    """Commit the next input revision of ``claim`` (mutated in place and saved)."""
    cid = claim["claim_id"]
    previous = claim["input_revision"] or None
    rev = (previous or 0) + 1
    photos, pages = file_refs(files)
    reused: list[ReusedArtifact] = state.reuse_artifacts(db, cid, reuse_from, reuse_stages) if reuse_from else []
    trace = trace_id or secrets.token_hex(8)
    vehicle = claim.get("vehicle") or {}
    vehicle_class = contract_vehicle_class(vehicle.get("vehicle_class"))
    payload: dict[str, Any] = {
        "previous_input_revision": previous, "external_reference": claim.get("reference") or None,
        "vehicle": {"make": vehicle.get("make") or "unknown", "model": vehicle.get("model") or "unknown",
                    "year": vehicle.get("year"), "vehicle_class": vehicle_class,
                    "class_reason": "vehicle_class_not_supplied" if vehicle_class == "unknown" else None},
        "currency": claim.get("currency", "SGD"), "photos": photos, "pages": pages}
    if reused:
        payload["reused_artifacts"] = [r.model_dump(mode="json") for r in reused]
    provenance = {"source_kind": source_kind, "runtime_profile": profile, "producer_service": SERVICE["api"]}
    message = build_message(INPUT_TOPIC, claim_id=cid, input_revision=rev, task="intake", versions=versions.intake,
                            provenance=provenance, trace_id=trace, occurred_at=now, payload=payload)
    record = {"claim_id": cid, "input_revision": rev, "previous_input_revision": previous,
              "file_ids": [f["file_id"] for f in files], "photo_ids": [p["file_id"] for p in photos],
              "page_targets": [page_target(p["file_id"], p["page_number"], p["media_type"] == "application/pdf")
                               for p in pages],
              "has_documents": bool(pages), "created_at": now.isoformat(), "source_kind": source_kind,
              "fixture_scenario": claim.get("fixture_scenario"), "fixture_source": (
                  f"fixture:{claim['fixture_scenario']}" if claim.get("fixture_scenario") else None),
              "corrections": [dict(c) for c in corrections], "base_assessment_revision": base_assessment_revision,
              "reused_artifacts": payload.get("reused_artifacts", []), "cost_table_version": cost_table_version,
              "review_revision": review_revision, "trace_id": trace}
    put(db, f"input:{cid}:{rev}", "input", record, cid)
    for item in files:
        stored = get(db, "file:" + item["file_id"])
        if stored is not None and stored.get("state") != "committed":
            put(db, "file:" + item["file_id"], "file", {**stored, "state": "committed"}, cid)
    state.create_branch_state(db, claim_id=cid, input_revision=rev, photo_count=len(photos), page_count=len(pages),
                              trace_id=trace, cost_table_version=cost_table_version, now=now,
                              review_revision=review_revision,
                              trigger="reassessment" if corrections else "branches_complete")
    key = state.create_intake_job(db, claim_id=cid, input_revision=rev, versions=versions.intake, message=message,
                                  now=now)
    claim["input_revision"] = rev
    put(db, "claim:" + cid, "claim", claim, cid)
    return {"input_revision": rev, "job_keys": [key], "state": "queued", "trace_id": trace,
            "reused_stages": sorted({r.kind.removesuffix("_records") for r in reused})}


__all__ = ["LEGACY_VEHICLE_CLASSES", "commit_input_revision", "contract_vehicle_class", "file_refs"]
