"""Validated evidence reads and transactional completion shared by M5/M6."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import PurePosixPath

from PIL import Image

from claim_cmev.contracts.common import Provenance
from claim_cmev.messaging.consumer import PermanentError, TransientError
from claim_cmev.messaging.outbox import build_message
from claim_cmev.orchestration import state
from claim_cmev.orchestration.plan import EVENT_TOPIC
from claim_cmev.persistence.store import load_records
from claim_cmev.vision.artifacts import read_object


def check_request(ctx, versions):
    env = ctx.envelope
    if state.is_superseded(ctx.session, env.claim_id, env.input_revision):
        raise PermanentError("superseded_input_revision", "a newer input revision supersedes this document request")
    if dict(env.versions) != dict(versions):
        raise PermanentError("model_version_unsupported", "command versions differ from the loaded worker")


def effective_records(ctx, stage):
    jobs = state.effective_jobs(ctx.session, ctx.envelope.claim_id, ctx.envelope.input_revision, stage)
    if not jobs:
        raise PermanentError("upstream_missing", f"no completed {stage} jobs for this revision")
    return load_records(ctx.session, ctx.envelope.claim_id, [j["job_key"] for j in jobs])


def read_verified(storage, ref):
    uri, prefix = ref.object_uri, storage.uri("")
    key = uri.removeprefix(prefix)
    if not uri.startswith(prefix) or not key or key.startswith("/") or ".." in PurePosixPath(key).parts:
        raise PermanentError("artifact_uri_invalid", "artifact is outside the configured object store")
    data = read_object(storage, uri, "document artifact")
    if hashlib.sha256(data).hexdigest() != ref.sha256 or len(data) != ref.byte_count:
        raise PermanentError("artifact_hash_mismatch", "document artifact bytes differ from the stored reference")
    return data


def load_pages(ctx, storage, *, reading):
    pages = effective_records(ctx, "page_read").get("document_page", [])
    by_id = {p.page_id: p for p in pages}
    requested = ctx.payload["pages"]
    if len(by_id) != len(pages) or len(requested) != len(pages) or {p["page_id"] for p in requested} != set(by_id):
        raise PermanentError("page_set_mismatch", "command must enumerate every completed M4 page exactly once")
    images = {}
    for record in requested:
        page = by_id[record["page_id"]]
        expected = page.model_dump(mode="json")
        if any(record[k] != expected[k] for k in record):
            raise PermanentError("artifact_metadata_mismatch", "command differs from the persisted M4 page")
        if page.quality.state == "unreadable":
            raise PermanentError("page_unreadable", "cannot treat an unreadable page as empty document evidence")
        if reading:
            read_verified(storage, page.page_reading_ref)
        data = read_verified(storage, page.corrected_render_ref)
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = source.convert("RGB")
                image.load()
            if image.size != (page.transform.corrected_width, page.transform.corrected_height):
                raise ValueError("corrected dimensions differ")
        except Exception as exc:
            raise PermanentError("artifact_image_invalid", "M4 corrected image is invalid") from exc
        images[page.page_id] = (data, image)
    return [by_id[p["page_id"]] for p in requested], images


def provenance(ctx, pages):
    # Never promote fixture/synthetic upstream records to real evidence.
    kinds = {p.provenance.source_kind for p in pages} | {ctx.source_kind}
    kind = "fixture" if "fixture" in kinds else "synthetic" if "synthetic" in kinds else "real"
    return Provenance.model_validate(ctx.provenance(source_kind=kind))


def write_artifact(ctx, storage, name, data, media_type):
    digest = hashlib.sha256(data).hexdigest()
    signature = hashlib.sha256(ctx.envelope.job_key.encode()).hexdigest()[:20]
    key = f"documents/{ctx.envelope.claim_id}/{ctx.envelope.input_revision}/{signature}/{digest[:16]}-{name}"
    try:
        storage.write(key, data, media_type)
    except Exception as exc:
        raise TransientError("artifact_write_failed", "could not persist document artifact") from exc
    return {"artifact_id": digest, "object_uri": storage.uri(key), "sha256": digest,
            "byte_count": len(data), "media_type": media_type}


def write_json(ctx, storage, name, record):
    return write_artifact(ctx, storage, name, (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode(),
                          "application/json")


def emit(ctx, stage, payload, prov):
    env, job = ctx.envelope, ctx.job
    topic = EVENT_TOPIC[stage]
    message = build_message(topic, claim_id=env.claim_id, input_revision=env.input_revision,
        task=job["task"], versions=env.versions, target=job["target"], attempt_epoch=job["attempt_epoch"],
        trace_id=env.trace_id, occurred_at=ctx.now, payload=payload, causation_id=env.dedup_key,
        provenance=prov.model_dump(mode="json"))
    ctx.emit(topic, message)
