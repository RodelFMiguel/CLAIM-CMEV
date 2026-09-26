"""cmev-api: real HTTP, persistence and outbox boundaries over a fixture-backed pipeline.

The API never publishes to Kafka and never computes a finding. An input-revision commit
writes the revision, its branch ledger, the intake job and the outbox event in one
transaction; the orchestrator, the fixture stage producers and the M8 consolidator do the
rest. Claim status, the current assessment and processing state are read models over
the tables those services write (``api/views.py``).
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import json
import logging
import secrets
import time
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, text

from ..contracts.common import COST_BASIS, ContractError
from ..contracts.documents import LineItem, PenMark
from ..costs.reference import CostTableError, load_table
from ..documents.pen_marks import MarkAction, apply_mark_action
from ..fixtures import seed
from ..messaging.outbox import DEFAULT_BACKLOG_LIMIT, backlog
from ..orchestration import state
from ..orchestration.intake import commit_input_revision
from ..orchestration.plan import VersionBundle
from ..orchestration.services import RuntimeSettings, local_pipeline
from ..persistence.migrations import EXPECTED_HEAD, check_head
from ..persistence.tables import dead_letters, idempotency_keys
from ..runtime import Database, Record, digest, get, put, setting, uid, utcnow
from ..storage import Storage
from . import views, review_service
from ..review import apply_review_action
from ..review.state import ReviewActionRequest

log = logging.getLogger("cmev.api")
COOKIE = "cmev_session"
DEMO_USER = {"id": "demo-surveyor", "name": "R. Miguel", "email": setting("DEMO_EMAIL", "surveyor@claim-cmev.demo"),
             "role": "surveyor", "initials": "RM"}
BACKLOG_LIMIT = int(setting("OUTBOX_BACKLOG_LIMIT", str(DEFAULT_BACKLOG_LIMIT)))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class ClaimCreate(StrictModel):
    reference: str = Field(min_length=1, max_length=100)
    vehicle_make: str = Field(min_length=1, max_length=80)
    vehicle_model: str = Field(min_length=1, max_length=100)
    vehicle_year: int = Field(ge=1950, le=2100)
    vehicle_class: Literal["hatchback_small", "sedan_standard", "suv_crossover", "van_commercial", "unknown",
                           "compact-sedan", "suv", "hatchback"] = "unknown"
    plate: str = Field(default="", max_length=30)
    policy_number: str = Field(default="", max_length=100)
    workshop: str = Field(default="", max_length=150)
    loss_date: date | None = None
    currency: str = Field(default="SGD", pattern="^[A-Z]{3}$")


class InputCreate(StrictModel):
    file_ids: list[str] = Field(min_length=1, max_length=50)


class Revision(StrictModel):
    expected_review_revision: int = Field(ge=0)


class Note(Revision):
    text: str = Field(min_length=1, max_length=4000)


class MarkDecision(Revision):
    expected_input_revision: int = Field(ge=1)
    decision: Literal["confirm", "reject"]
    amount: str | None = Field(default=None, max_length=30)
    entry_id: str | None = Field(default=None, max_length=128)


def fail(status, code, message, headers=None, **extra):
    raise HTTPException(status, {"reason_code": code, "message": message, **extra}, headers=headers)


def claim_for(db, cid, lock=False):
    row = db.scalar(select(Record).where(Record.key == "claim:" + cid).with_for_update()) if lock \
        else db.get(Record, "claim:" + cid)
    if row is None or row.data.get("owner_id") != DEMO_USER["id"]:
        fail(404, "claim_not_found", "Claim not found.")
    return deepcopy(row.data)


def assessment_for(db, cid, rev):
    row = views.assessment_row(db, cid, rev)
    if row is None:
        fail(404, "assessment_not_found", "Assessment not found.")
    return row


def idem(db, request, payload, action, claim_id="-"):
    """``ops.idempotency_keys``: same key and body replays; same key, other body is 409."""
    key = request.headers.get("Idempotency-Key", "")
    if not 1 <= len(key) <= 160:
        fail(400, "idempotency_key_required", "Supply an Idempotency-Key header.")
    endpoint = request.url.path[:300]
    body_hash = digest(json.dumps(payload, sort_keys=True, default=str))
    previous = db.execute(select(idempotency_keys).where(
        idempotency_keys.c.claim_id == claim_id, idempotency_keys.c.endpoint == endpoint,
        idempotency_keys.c.idempotency_key == key)).mappings().first()
    if previous:
        if previous["request_hash"] != body_hash:
            fail(409, "idempotency_conflict", "This request key was already used with different values.")
        return previous["response"]
    response = action()
    db.execute(insert(idempotency_keys).values(claim_id=claim_id, endpoint=endpoint, idempotency_key=key,
                                               actor=DEMO_USER["id"], request_hash=body_hash, response=response,
                                               created_at=utcnow()))
    return response


def _decimal_amount(raw):
    try:
        value = Decimal(raw or "NaN")
        if not value.is_finite() or value <= 0 or value > 999999999 or value.as_tuple().exponent < -2:
            raise InvalidOperation
        return format(value, ".2f")
    except InvalidOperation:
        fail(422, "amount_required", "Enter a positive decimal amount with at most two decimal places.")


def create_app(database_url=None, storage_path=None):
    database = Database(database_url)
    storage = Storage(storage_path)
    settings = RuntimeSettings.from_env()
    versions = VersionBundle.fixture()
    allowed = [s.strip() for s in setting("ALLOWED_ORIGINS", "http://localhost:8080,http://localhost:5173,"
                                          "http://127.0.0.1:5173").split(",")]
    failures = {}

    def pinned_table():
        try:
            return settings.active_cost_table()
        except CostTableError as exc:
            fail(503, "cost_table_unavailable", "No reference cost table is available for new assessments.",
                 headers={"Retry-After": "30"}, detail=exc.reason_code)

    def commit(db, claim, files, **options):
        return commit_input_revision(db, claim, files, now=utcnow(), versions=versions,
                                     cost_table_version=options.pop("cost_table_version", None) or pinned_table(),
                                     profile=settings.profile, source_kind=settings.source_kind, **options)

    def freeze(db, claim_id, rev=None):
        claim = get(db, "claim:" + claim_id)
        rev = rev or views.claim_view(db, claim)["assessment_revision"]
        if rev is None:
            return None
        row = views.assessment_row(db, claim_id, rev)
        review = views.review_for(db, claim, rev)
        result, frozen = review_service.freeze(db, claim, row, review, actor=DEMO_USER["name"],
                                              expected=claim["review_revision"])
        if result.http_status != 200:
            fail(result.http_status, result.reason_code, result.message,
                 preconditions=result.preconditions.model_dump(mode="json") if result.preconditions else None)
        return frozen

    def drain_seeds():
        if setting("SEED_PIPELINE", "inline") != "inline":
            return
        try:
            local_pipeline(database, settings=settings, sleep=lambda _s: None).drain()
        except Exception:  # noqa: BLE001 - seeds then wait for the worker; never a fabricated result
            log.exception("inline seed processing failed; seed claims stay queued for the worker")

    @asynccontextmanager
    async def lifespan(app):
        if not settings.fixture_mode:
            raise RuntimeError("Only fixture mode is implemented; real model execution is unavailable")
        database.initialize()
        storage.initialize()
        if setting("SEED", "true").lower() == "true":
            try:
                settings.active_cost_table()
            except CostTableError:  # readiness reports it; seeds are created on a later start
                log.warning("no active cost table under %s; demonstration seeds not created", settings.cost_table_root)
            else:
                seed(database, commit=lambda db, claim, files: commit(db, claim, files), drain=drain_seeds,
                     finalize=lambda db, cid: freeze(db, cid))
        yield

    app = FastAPI(title="CLAIM-CMEV API", version="0.2.0", lifespan=lifespan,
                  docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json", redoc_url="/api/v1/redoc",
                  description="Persistent APIs over an event-driven pipeline. Stage outputs are labelled fixtures.")
    app.state.database = database
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=True, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "Idempotency-Key"])

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        body = exc.detail if isinstance(exc.detail, dict) else {"reason_code": "http_error", "message": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"reason_code": "invalid_request", "message": "Check the required fields and their formats."},
                            status_code=422)

    @app.middleware("http")
    async def session_guard(request, call_next):
        public = {"/api/v1/auth/login", "/api/v1/healthz", "/api/v1/readyz", "/api/v1/version", "/api/v1/docs",
                  "/api/v1/openapi.json", "/api/v1/redoc"}
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if origin and origin not in allowed and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"reason_code": "origin_not_allowed", "message": "Request origin is not allowed."},
                                    status_code=403)
        if request.url.path not in public:
            token = request.cookies.get(COOKIE)
            with database.session() as db:
                session = get(db, "session:" + digest(token)) if token else None
            if not session or session["expires_at"] < time.time():
                return JSONResponse({"reason_code": "authentication_required", "message": "Please sign in."},
                                    status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/v1/healthz")
    def health():
        return {"status": "ok", "fixture_mode": True}

    @app.get("/api/v1/readyz")
    def ready():
        detail = {"database": "unavailable", "schema": "unknown", "storage": "unavailable", "cost_table": "unavailable"}
        failing = []
        try:
            with database.session() as db:
                db.execute(text("SELECT 1"))
            detail["database"] = "ready"
            detail["schema"] = check_head(database.engine)
        except Exception:  # noqa: BLE001
            failing.append("database_or_schema")
        try:
            storage.ready()
            detail["storage"] = "ready"
        except Exception:  # noqa: BLE001
            failing.append("storage")
        try:
            version = settings.active_cost_table()
            load_table(settings.cost_table_root, version)
            detail["cost_table"] = version
        except Exception:  # noqa: BLE001
            failing.append("cost_table")
        if failing:
            fail(503, "dependency_unavailable", "A required dependency is not ready.", failing=failing, **detail)
        return {"status": "ready", **detail, "fixture_mode": True}

    @app.get("/api/v1/version")
    def version():
        return {"version": "0.2.0", "schema_version": "0.2.0", "fixture_mode": True, "runtime_profile": settings.profile,
                "expected_schema_head": EXPECTED_HEAD, "stage_versions": {s: versions.for_stage(s) for s in versions.stages}}

    @app.post("/api/v1/auth/login")
    def login(body: Login, request: Request, response: Response):
        peer = request.client.host if request.client else "unknown"
        attempt_key = (peer, body.email.casefold())
        recent = [t for t in failures.get(attempt_key, []) if time.time() - t < 60]
        if len(recent) >= 10:
            fail(429, "login_rate_limited", "Too many attempts. Try again in a minute.")
        valid = secrets.compare_digest(body.email.lower().encode(), DEMO_USER["email"].lower().encode()) & secrets.compare_digest(
            body.password.encode(), setting("DEMO_PASSWORD", "Demo2026!").encode())
        if not valid:
            failures[attempt_key] = recent + [time.time()]
            fail(401, "invalid_credentials", "The email or password is incorrect.")
        failures.pop(attempt_key, None)
        token = secrets.token_urlsafe(48)
        with database.session.begin() as db:
            old = request.cookies.get(COOKIE)
            if old and (row := db.get(Record, "session:" + digest(old))):
                db.delete(row)
            put(db, "session:" + digest(token), "session", {"user_id": DEMO_USER["id"], "expires_at": time.time() + 28800})
        response.set_cookie(COOKIE, token, httponly=True, secure=setting("COOKIE_SECURE", "false").lower() == "true",
                            samesite="lax", max_age=28800, path="/")
        return {"user": DEMO_USER, "demo_mode": True}

    @app.get("/api/v1/auth/me")
    def me():
        return {"user": DEMO_USER, "demo_mode": True}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response):
        with database.session.begin() as db:
            row = db.get(Record, "session:" + digest(request.cookies.get(COOKIE, "")))
            if row:
                db.delete(row)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/v1/claims")
    def claims(q: str = ""):
        with database.session() as db:
            all_items = views.claim_views(db, [r.data for r in db.scalars(select(Record).where(Record.kind == "claim")
                                                                          .order_by(Record.key))
                         if r.data.get("owner_id") == DEMO_USER["id"]])
        items = [c for c in all_items if not q or q.lower() in json.dumps(c).lower()]
        return {"items": items, "total": len(items),
                "stats": {"open_claims": sum(c["status"] != "ready_to_print" for c in all_items),
                          "open_findings": sum(c["finding_count"] for c in all_items),
                          "photographs_received": sum(c["photograph_count"] for c in all_items)}}

    @app.post("/api/v1/claims", status_code=201)
    def create_claim(body: ClaimCreate, request: Request):
        payload = body.model_dump(mode="json")
        with database.session.begin() as db:
            # The seed marker row serializes duplicate-reference checks across requests.
            db.scalar(select(Record).where(Record.key.like("seed:%")).with_for_update())

            def action():
                if any(r.data["reference"] == body.reference.strip()
                       for r in db.scalars(select(Record).where(Record.kind == "claim"))):
                    fail(409, "reference_exists", "A claim already uses this reference.")
                cid = uid()
                claim = {"claim_id": cid, "reference": body.reference.strip(), "policy_number": body.policy_number,
                         "surveyor": DEMO_USER["name"], "owner_id": DEMO_USER["id"],
                         "vehicle": {"make": body.vehicle_make, "model": body.vehicle_model, "year": body.vehicle_year,
                                     "plate": body.plate, "vehicle_class": body.vehicle_class},
                         "workshop": body.workshop, "loss_date": payload["loss_date"], "status": "awaiting_upload",
                         "currency": body.currency, "input_revision": 0, "review_revision": 0, "source_kind": "fixture",
                         "created_at": utcnow().isoformat()}
                put(db, "claim:" + cid, "claim", claim, cid)
                return views.claim_view(db, claim)
            return idem(db, request, payload, action)

    @app.get("/api/v1/claims/{cid}")
    def detail(cid: str):
        with database.session() as db:
            return views.claim_view(db, claim_for(db, cid))

    @app.post("/api/v1/claims/{cid}/files", status_code=201)
    async def upload(cid: str, request: Request, files: list[UploadFile] = File(...),
                     role: Literal["photograph", "estimate_page"] = Form(...)):
        if len(files) > (40 if role == "photograph" else 10):
            fail(413, "file_count_exceeded", "Too many files.")
        checked = []
        for file in files:
            raw = await file.read(25 * 1024 * 1024 + 1)
            if len(raw) > 25 * 1024 * 1024:
                fail(413, "file_too_large", "The per-file limit is 25 MB.")
            media = ("application/pdf" if raw.startswith(b"%PDF-") else "image/png"
                     if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None)
            if media is None or media != file.content_type or (role == "photograph" and media == "application/pdf"):
                fail(415, "unsupported_media", "Upload a matching JPEG, PNG, or estimate PDF.")
            geometry = {}
            try:
                if media == "application/pdf":
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(raw))
                    if reader.is_encrypted or not 1 <= len(reader.pages) <= 10:
                        fail(422, "pdf_pages_invalid", "Supply an unencrypted PDF with 1 to 10 pages.")
                    sizes = [[max(1, int(float(p.mediabox.width))), max(1, int(float(p.mediabox.height)))]
                             for p in reader.pages]
                    geometry = {"page_count": len(sizes), "page_sizes": sizes, "width": sizes[0][0],
                                "height": sizes[0][1], "exif_orientation": None}
                else:
                    image = Image.open(BytesIO(raw))
                    image.verify()
                    image = Image.open(BytesIO(raw))
                    orientation = image.getexif().get(274)
                    geometry = {"page_count": 1, "width": image.width, "height": image.height,
                                "exif_orientation": orientation if orientation in range(1, 9) else None}
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                fail(422, "unreadable_file", "The file could not be decoded.")
            checked.append((file.filename or "upload", media, raw, hashlib.sha256(raw).hexdigest(), geometry))
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)

            def action():
                output = []
                for name, media, raw, sha, geometry in checked:
                    existing = next((r.data for r in db.scalars(select(Record).where(Record.kind == "file",
                                                                                     Record.claim_id == cid))
                                     if r.data["sha256"] == sha and r.data["role"] == role), None)
                    if existing:
                        output.append(existing)
                        continue
                    fid = uid()
                    storage.write(fid, raw, media)
                    item = {"file_id": fid, "claim_id": cid, "original_name": name[:255], "media_type": media,
                            "byte_count": len(raw), "sha256": sha, "role": role, **geometry,
                            "object_uri": storage.uri(fid), "state": "staged",
                            "url": f"/api/v1/claims/{cid}/evidence/{fid}"}
                    put(db, "file:" + fid, "file", item, cid)
                    output.append(item)
                return {"files": output}
            return idem(db, request, {"role": role, "files": [[x[0], x[3]] for x in checked]}, action, cid)

    @app.get("/api/v1/claims/{cid}/files")
    def files_list(cid: str):
        with database.session() as db:
            claim_for(db, cid)
            return {"files": [r.data for r in db.scalars(select(Record).where(Record.kind == "file", Record.claim_id == cid))
                              if not r.data.get("fixture_placeholder")]}

    @app.get("/api/v1/claims/{cid}/evidence/{fid}")
    def evidence(cid: str, fid: str):
        with database.session() as db:
            claim_for(db, cid)
            item = get(db, "file:" + fid)
            if not item:
                fail(404, "evidence_not_found", "Evidence not found.")
            if item["claim_id"] != cid:
                fail(403, "evidence_claim_mismatch", "Evidence belongs to another claim.")
            if item.get("fixture_placeholder"):
                fail(404, "fixture_original_absent", "This fixture reference has no stored original.")
        return Response(storage.read(fid), media_type=item["media_type"],
                        headers={"ETag": '"' + item["sha256"] + '"', "Content-Disposition": "inline"})

    @app.post("/api/v1/claims/{cid}/input-revisions", status_code=202)
    def commit_input(cid: str, body: InputCreate, request: Request):
        with database.session.begin() as db:
            c = claim_for(db, cid, lock=True)

            def action():
                if len(body.file_ids) != len(set(body.file_ids)):
                    fail(422, "duplicate_file_ids", "Each file may appear only once.")
                records = [get(db, "file:" + fid) for fid in body.file_ids]
                if any(not f or f["claim_id"] != cid or f.get("fixture_placeholder") for f in records):
                    fail(409, "unknown_file", "Every file must belong to this claim.")
                photos = sum(f["role"] == "photograph" for f in records)
                pages = sum(f.get("page_count", 1) for f in records if f["role"] == "estimate_page")
                if photos > 40 or pages > 10:
                    fail(422, "input_limit_exceeded", "An input supports 40 photographs and 10 estimate pages.")
                if not photos and not pages:
                    fail(422, "input_empty", "Supply at least one photograph or estimate page.")
                if backlog(db) >= BACKLOG_LIMIT:
                    fail(503, "dispatch_backlog", "Processing backlog is full. Please retry later.",
                         headers={"Retry-After": "30"})
                c["photograph_count"] = photos
                previous = get(db, f"input:{cid}:{c['input_revision']}") or {}
                pointer = views.pointer_row(db, cid)
                old_files = [get(db, "file:" + fid) for fid in previous.get("file_ids", [])]
                unchanged = {role: [f["file_id"] for f in old_files if f and f["role"] == role] ==
                             [f["file_id"] for f in records if f["role"] == role]
                             for role in ("photograph", "estimate_page")}
                reuse = []
                if unchanged["photograph"]:
                    reuse.extend(("parts", "damage", "summary"))
                if unchanged["estimate_page"]:
                    reuse.extend(("page_read", "line_items", "pen_marks"))
                corrections, invalidated = [], []
                for correction in previous.get("corrections", []):
                    action_type = correction.get("event", {}).get("action_type", correction.get("action_type"))
                    role = "photograph" if action_type in ("confirm_identity", "confirm_coverage") else "estimate_page"
                    (corrections if unchanged[role] else invalidated).append(correction)
                result = commit(db, c, records, corrections=corrections,
                                base_assessment_revision=pointer["assessment_revision"] if pointer else None,
                                reuse_from=previous.get("input_revision"), reuse_stages=reuse,
                                cost_table_version=previous.get("cost_table_version"),
                                review_revision=c["review_revision"])
                if invalidated:
                    key = f"input:{cid}:{result['input_revision']}"
                    new_input = get(db, key)
                    new_input["invalidated_corrections"] = invalidated
                    new_input["invalidation_reason"] = "source_evidence_changed_requires_review"
                    put(db, key, "input", new_input, cid)
                return {"input_revision": result["input_revision"], "job_keys": result["job_keys"], "state": "queued",
                        "trace_id": result["trace_id"]}
            return idem(db, request, body.model_dump(), action, cid)

    @app.get("/api/v1/claims/{cid}/input-revisions/{rev}")
    def input_revision(cid: str, rev: int):
        with database.session() as db:
            claim_for(db, cid)
            result = get(db, f"input:{cid}:{rev}")
            if result is None:
                fail(404, "input_not_found", "Input revision not found.")
            return result

    @app.get("/api/v1/claims/{cid}/processing")
    def processing(cid: str, input_revision: int | None = None):
        with database.session() as db:
            return views.processing_view(db, claim_for(db, cid), input_revision)

    @app.get("/api/v1/claims/{cid}/jobs")
    def job_list(cid: str, input_revision: int | None = None):
        with database.session() as db:
            view = views.processing_view(db, claim_for(db, cid), input_revision)
            return {"input_revision": view["input_revision"], "items": view["jobs"], "jobs": view["jobs"]}

    @app.post("/api/v1/claims/{cid}/jobs/{job_key}/retry")
    def retry(cid: str, job_key: str, request: Request):
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)

            def action():
                job = state.job_row(db, job_key)
                if job is None or job["claim_id"] != cid:
                    fail(404, "job_not_found", "Job not found.")
                try:
                    updated = state.retry_job(db, job_key, now=utcnow())
                except state.JobConflict as exc:
                    fail(409, exc.reason_code, str(exc))
                except state.JobNotRetryable as exc:
                    fail(422, exc.reason_code, str(exc))
                return {"job_key": job_key, "state": updated["state"], "attempt_epoch": updated["attempt_epoch"],
                        "attempt_count": updated["attempt_count"]}
            return idem(db, request, {"job_key": job_key}, action, cid)

    @app.get("/api/v1/claims/{cid}/assessments")
    def assessments(cid: str):
        with database.session() as db:
            claim_v = views.claim_view(db, claim_for(db, cid))
            return {"items": [views.assessment_summary(claim_v, r) for r in views.assessment_rows(db, cid)]}

    @app.get("/api/v1/claims/{cid}/assessments/{rev}")
    def assessment_detail(cid: str, rev: int):
        with database.session() as db:
            claim = claim_for(db, cid)
            return views.assessment_view(db, claim, assessment_for(db, cid, rev))

    @app.get("/api/v1/claims/{cid}/assessments/{rev}/review")
    def review_detail(cid: str, rev: int):
        with database.session() as db:
            claim = claim_for(db, cid)
            assessment_for(db, cid, rev)
            return review_service.public(views.review_for(db, claim, rev))

    def review_context(db, cid, rev, expected):
        c = claim_for(db, cid, lock=True)
        row = assessment_for(db, cid, rev)
        claim_v = views.claim_view(db, c)
        review = views.review_for(db, c, rev)
        if c["review_revision"] != expected or claim_v["assessment_revision"] != rev \
                or c["input_revision"] != row["input_revision"]:
            fail(409, "stale_revision", "Reload the current assessment before saving.",
                 current_revision=c["review_revision"], submitted_revision=expected)
        if review["finalized"]:
            fail(409, "review_finalized", "This review is frozen.")
        return c, row, review

    def perform_review(cid, rev, body, request):
        with database.session.begin() as db:
            c = claim_for(db, cid, lock=True)
            row = assessment_for(db, cid, rev)
            review = views.review_for(db, c, rev)

            def action():
                # Wait for the previous decision to finish before editing its old assessment.
                if c["input_revision"] != row["input_revision"]:
                    fail(409, "reassessment_pending", "Wait for the current reassessment before saving.")
                outcome = apply_review_action(
                    review_service.load(db, c, row, review), body, actor=DEMO_USER["name"],
                    recorded_at=utcnow(), idempotency_key=digest(request.url.path + "|" + request.headers.get("Idempotency-Key", "")),
                    action_id="ra-" + uid().lower())
                if not outcome.ok:
                    details = outcome.response()
                    details.pop("message", None)
                    details.pop("reason_code", None)
                    fail(outcome.http_status, outcome.reason_code, outcome.message, **details)
                review_service.save_action(db, c, row, review, outcome)
                response = outcome.response()
                if outcome.plan:
                    previous = get(db, f"input:{cid}:{c['input_revision']}")
                    files = [get(db, "file:" + fid) for fid in previous["file_ids"]]
                    correction = {"kind": "review_event", "event": outcome.event.model_dump(mode="json")}
                    result = commit(db, c, files, corrections=[*previous.get("corrections", []), correction],
                                    base_assessment_revision=rev, reuse_from=row["input_revision"],
                                    reuse_stages=outcome.plan.reused_stages,
                                    review_revision=outcome.review_revision,
                                    cost_table_version=row["cost_table_version"])
                    response.update(input_revision=result["input_revision"], assessment_revision=None, state="queued",
                                    job_keys=result["job_keys"], reused_stages=result["reused_stages"])
                return response
            response = idem(db, request, body.model_dump(mode="json"), action, cid)
            return JSONResponse(response, status_code=202 if response.get("new_input_revision") else 200)

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/review-actions")
    def review_action(cid: str, rev: int, body: ReviewActionRequest, request: Request):
        return perform_review(cid, rev, body, request)

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/notes")
    def note(cid: str, rev: int, body: Note, request: Request):
        return perform_review(cid, rev, ReviewActionRequest(
            action_type="add_note", expected_review_revision=body.expected_review_revision, note=body.text), request)

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/marks/{mark_id}/decision", status_code=202)
    def mark_decision(cid: str, rev: int, mark_id: str, body: MarkDecision, request: Request):
        if body.decision == "reject" and body.amount not in (None, ""):
            fail(422, "amount_not_allowed", "Only a confirmed price change carries an amount.")
        return perform_review(cid, rev, ReviewActionRequest(
            action_type="confirm_mark" if body.decision == "confirm" else "reject_mark", mark_id=mark_id,
            expected_review_revision=body.expected_review_revision, expected_input_revision=body.expected_input_revision,
            target_entry_id=body.entry_id if body.decision == "confirm" else None,
            amount=_decimal_amount(body.amount) if body.amount else None), request)

    @app.get("/api/v1/claims/{cid}/assessments/{rev}/finalize-preconditions")
    def finalize_preconditions(cid: str, rev: int):
        with database.session() as db:
            claim = claim_for(db, cid)
            row = assessment_for(db, cid, rev)
            return views.preconditions(db, views.claim_view(db, claim), row, views.review_for(db, claim, rev))

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/finalize")
    def finalize(cid: str, rev: int, body: Revision, request: Request):
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)

            def action():
                c, row, review = review_context(db, cid, rev, body.expected_review_revision)
                checks = views.preconditions(db, views.claim_view(db, c), row, review)
                if not checks["can_finalize"]:
                    fail(412, "finalize_blocked", "Resolve the remaining review requirements.", preconditions=checks)
                frozen = freeze(db, cid, rev)
                return {"review_revision": frozen["review_revision"], "assessment_revision": rev,
                        "print_view_url": f"/api/v1/claims/{cid}/assessments/{rev}/print-view"
                                          f"?review_revision={frozen['review_revision']}"}
            return idem(db, request, body.model_dump(), action, cid)

    @app.get("/api/v1/claims/{cid}/assessments/{rev}/print-view")
    def print_view(cid: str, rev: int, review_revision: int):
        with database.session() as db:
            claim_for(db, cid)
            assessment_for(db, cid, rev)
            review = get(db, f"review:{cid}:{rev}")
            if not review or not review["finalized"]:
                fail(412, "not_finalized", "Finalize this review before printing.")
            if review_revision != review["review_revision"]:
                fail(409, "review_revision_mismatch", "The requested frozen review does not match.")
            frozen = review_service.public(review)
            assessment = review["assessment_snapshot"]
            return {"claim": review["claim_snapshot"], "assessment": assessment, "review": frozen, "report": review.get("report"),
                    "disclaimer": assessment["fixture_notice"] or "Synthetic reference costs; not a final approval."}

    @app.get("/api/v1/admin/dead-letters")
    def dead_letter_list(claim_id: str | None = None):
        with database.session() as db:
            query = select(dead_letters).order_by(dead_letters.c.id)
            if claim_id:
                claim_for(db, claim_id)
                query = query.where(dead_letters.c.claim_id == claim_id)
            rows = db.execute(query).mappings().all()
            return {"items": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()
                               if k != "message"} for r in rows]}

    return app


app = create_app()
