"""Real HTTP/persistence boundaries with clearly labelled fixture processing."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import json
import secrets
import time
from typing import Literal
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, text
from ..runtime import Database, Record, Outbox, get, put, setting, uid, digest, now, enqueue
from ..storage import Storage
from ..fixtures import seed

COOKIE = "cmev_session"
DEMO_USER = {"id": "demo-surveyor", "name": "R. Miguel", "email": setting("DEMO_EMAIL", "surveyor@claim-cmev.demo"), "role": "surveyor", "initials": "RM"}


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
    vehicle_class: Literal["compact-sedan", "suv", "hatchback", "unknown"] = "unknown"
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


def fail(status, code, message, **extra):
    raise HTTPException(status, {"reason_code": code, "message": message, **extra})


def claim_for(db, cid, lock=False):
    row = db.scalar(select(Record).where(Record.key == "claim:" + cid).with_for_update()) if lock else db.get(Record, "claim:" + cid)
    if row is None or row.data.get("owner_id") != DEMO_USER["id"]:
        fail(404, "claim_not_found", "Claim not found.")
    return deepcopy(row.data)


def assessment_for(db, cid, rev):
    result = get(db, f"assessment:{cid}:{rev}")
    if result is None:
        fail(404, "assessment_not_found", "Assessment not found.")
    return deepcopy(result)


def idem(db, request, payload, action):
    key = request.headers.get("Idempotency-Key", "")
    if not 1 <= len(key) <= 160:
        fail(400, "idempotency_key_required", "Supply an Idempotency-Key header.")
    scope = DEMO_USER["id"] + request.url.path + key
    identity = "idem:" + digest(scope)
    body_hash = digest(json.dumps(payload, sort_keys=True, default=str))
    previous = get(db, identity)
    if previous:
        if previous["hash"] != body_hash:
            fail(409, "idempotency_conflict", "This request key was already used with different values.")
        return previous["response"]
    response = action()
    put(db, identity, "idempotency", {"hash": body_hash, "response": response})
    return response


def preconditions(claim, assessment, review):
    current = claim["assessment_revision"] == assessment["assessment_revision"] and claim["input_revision"] == assessment["input_revision"]
    checks = [
        {"code": "assessment_current", "passed": current, "message": "The assessment must match the current input revision."},
        {"code": "assessment_completed", "passed": assessment["state"] == "completed" and claim["status"] in ("in_review", "ready_to_print"), "message": "Processing must be complete."},
        {"code": "marks_resolved", "passed": all(m["state"] in ("confirmed", "rejected") and m.get("entry_id") in {r["entry_id"] for r in assessment["line_items"]} for m in assessment["marks"]), "message": "Confirm or reject every pending pen mark."},
        {"code": "effective_amounts_resolved", "passed": all(r["effective_amount"] is not None for r in assessment["line_items"]), "message": "Enter an effective amount for each included line item."},
    ]
    return {"can_finalize": all(c["passed"] for c in checks), "checks": checks}


def create_app(database_url=None, storage_path=None):
    database = Database(database_url)
    storage = Storage(storage_path)
    allowed = [s.strip() for s in setting("ALLOWED_ORIGINS", "http://localhost:8080,http://localhost:5173,http://127.0.0.1:5173").split(",")]
    failures = {}

    @asynccontextmanager
    async def lifespan(app):
        if setting("FIXTURE_MODE", "true").lower() != "true":
            raise RuntimeError("Only fixture mode is implemented; real model execution is unavailable")
        database.initialize()
        storage.initialize()
        with database.session.begin() as db:
            seed(db)
        yield

    app = FastAPI(title="CLAIM-CMEV baseline API", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json", redoc_url="/api/v1/redoc",
                  description="Persistent APIs. All assessments are explicitly labelled fixture results.")
    app.state.database = database
    app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Idempotency-Key"])

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(exc.detail if isinstance(exc.detail, dict) else {"reason_code": "http_error", "message": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"reason_code": "invalid_request", "message": "Check the required fields and their formats."}, status_code=422)

    @app.middleware("http")
    async def session_guard(request, call_next):
        public = {"/api/v1/auth/login", "/api/v1/healthz", "/api/v1/readyz", "/api/v1/version", "/api/v1/docs", "/api/v1/openapi.json", "/api/v1/redoc"}
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if origin and origin not in allowed and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"reason_code": "origin_not_allowed", "message": "Request origin is not allowed."}, status_code=403)
        if request.url.path not in public:
            token = request.cookies.get(COOKIE)
            with database.session() as db:
                session = get(db, "session:" + digest(token)) if token else None
            if not session or session["expires_at"] < time.time():
                return JSONResponse({"reason_code": "authentication_required", "message": "Please sign in."}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/v1/healthz")
    def health():
        return {"status": "ok", "fixture_mode": True}

    @app.get("/api/v1/readyz")
    def ready():
        try:
            with database.session() as db:
                db.execute(text("SELECT 1"))
            storage.ready()
        except Exception:
            fail(503, "dependency_unavailable", "Database or evidence storage is unavailable.")
        return {"status": "ready", "database": "ready", "storage": "ready", "fixture_mode": True}

    @app.get("/api/v1/version")
    def version():
        return {"version": "0.1.0", "schema_version": "0.2.0", "fixture_mode": True, "runtime_profile": "lean"}

    @app.post("/api/v1/auth/login")
    def login(body: Login, request: Request, response: Response):
        peer = request.client.host if request.client else "unknown"
        recent = [t for t in failures.get(peer, []) if time.time() - t < 60]
        if len(recent) >= 10:
            fail(429, "login_rate_limited", "Too many attempts. Try again in a minute.")
        valid = secrets.compare_digest(body.email.lower(), DEMO_USER["email"].lower()) & secrets.compare_digest(body.password, setting("DEMO_PASSWORD", "Demo2026!"))
        if not valid:
            failures[peer] = recent + [time.time()]
            fail(401, "invalid_credentials", "The email or password is incorrect.")
        failures.pop(peer, None)
        token = secrets.token_urlsafe(48)
        with database.session.begin() as db:
            old = request.cookies.get(COOKIE)
            if old and (row := db.get(Record, "session:" + digest(old))):
                db.delete(row)
            put(db, "session:" + digest(token), "session", {"user_id": DEMO_USER["id"], "expires_at": time.time() + 28800})
        response.set_cookie(COOKIE, token, httponly=True, secure=setting("COOKIE_SECURE", "false").lower() == "true", samesite="lax", max_age=28800, path="/")
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
            all_items = [r.data for r in db.scalars(select(Record).where(Record.kind == "claim").order_by(Record.key)) if r.data.get("owner_id") == DEMO_USER["id"]]
        items = [c for c in all_items if not q or q.lower() in json.dumps(c).lower()]
        return {"items": items, "total": len(items), "stats": {"open_claims": sum(c["status"] != "ready_to_print" for c in all_items), "open_findings": sum(c["finding_count"] for c in all_items), "photographs_received": sum(c["photograph_count"] for c in all_items)}}

    @app.post("/api/v1/claims", status_code=201)
    def create_claim(body: ClaimCreate, request: Request):
        payload = body.model_dump(mode="json")
        with database.session.begin() as db:
            # A per-user lock also serializes duplicate reference and idempotency checks.
            db.scalar(select(Record).where(Record.key == "seed:baseline-v1").with_for_update())
            def action():
                if any(r.data["reference"] == body.reference.strip() for r in db.scalars(select(Record).where(Record.kind == "claim"))):
                    fail(409, "reference_exists", "A claim already uses this reference.")
                cid = uid()
                claim = {"claim_id": cid, "reference": body.reference.strip(), "policy_number": body.policy_number, "surveyor": DEMO_USER["name"],
                    "owner_id": DEMO_USER["id"], "vehicle": {"make": body.vehicle_make, "model": body.vehicle_model, "year": body.vehicle_year, "plate": body.plate, "vehicle_class": body.vehicle_class},
                    "workshop": body.workshop, "loss_date": payload["loss_date"], "status": "awaiting_upload", "photograph_count": 0,
                    "estimate_row_count": 0, "declared_total": None, "currency": body.currency, "finding_count": 0,
                    "input_revision": 0, "assessment_revision": None, "review_revision": 0, "source_kind": "fixture", "created_at": now()}
                return put(db, "claim:" + cid, "claim", claim, cid)
            return idem(db, request, payload, action)

    @app.get("/api/v1/claims/{cid}")
    def detail(cid: str):
        with database.session() as db:
            return claim_for(db, cid)

    @app.post("/api/v1/claims/{cid}/files", status_code=201)
    async def upload(cid: str, request: Request, files: list[UploadFile] = File(...), role: Literal["photograph", "estimate_page"] = Form(...)):
        if len(files) > (40 if role == "photograph" else 10):
            fail(413, "file_count_exceeded", "Too many files.")
        checked = []
        for file in files:
            raw = await file.read(25 * 1024 * 1024 + 1)
            if len(raw) > 25 * 1024 * 1024:
                fail(413, "file_too_large", "The per-file limit is 25 MB.")
            media = "application/pdf" if raw.startswith(b"%PDF-") else "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None
            if media is None or media != file.content_type or (role == "photograph" and media == "application/pdf"):
                fail(415, "unsupported_media", "Upload a matching JPEG, PNG, or estimate PDF.")
            try:
                if media == "application/pdf":
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(raw))
                    if reader.is_encrypted or not 1 <= len(reader.pages) <= 10:
                        fail(422, "pdf_pages_invalid", "Supply an unencrypted PDF with 1 to 10 pages.")
                    page_count = len(reader.pages)
                else:
                    image = Image.open(BytesIO(raw))
                    image.verify()
                    page_count = 1
            except HTTPException:
                raise
            except Exception:
                fail(422, "unreadable_file", "The file could not be decoded.")
            checked.append((file.filename or "upload", media, raw, hashlib.sha256(raw).hexdigest(), page_count))
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)
            def action():
                output = []
                for name, media, raw, sha, page_count in checked:
                    existing = next((r.data for r in db.scalars(select(Record).where(Record.kind == "file", Record.claim_id == cid)) if r.data["sha256"] == sha and r.data["role"] == role), None)
                    if existing:
                        output.append(existing)
                        continue
                    fid = uid()
                    storage.write(fid, raw, media)
                    item = {"file_id": fid, "claim_id": cid, "original_name": name[:255], "media_type": media, "byte_count": len(raw), "sha256": sha,
                        "role": role, "page_count": page_count, "state": "staged", "url": f"/api/v1/claims/{cid}/evidence/{fid}"}
                    put(db, "file:" + fid, "file", item, cid)
                    output.append(item)
                return {"files": output}
            return idem(db, request, {"role": role, "files": [[x[0], x[3]] for x in checked]}, action)

    @app.get("/api/v1/claims/{cid}/files")
    def files_list(cid: str):
        with database.session() as db:
            claim_for(db, cid)
            return {"files": [r.data for r in db.scalars(select(Record).where(Record.kind == "file", Record.claim_id == cid))]}

    @app.get("/api/v1/claims/{cid}/evidence/{fid}")
    def evidence(cid: str, fid: str):
        with database.session() as db:
            claim_for(db, cid)
            item = get(db, "file:" + fid)
            if not item:
                fail(404, "evidence_not_found", "Evidence not found.")
            if item["claim_id"] != cid:
                fail(403, "evidence_claim_mismatch", "Evidence belongs to another claim.")
        return Response(storage.read(fid), media_type=item["media_type"], headers={"ETag": '"' + item["sha256"] + '"', "Content-Disposition": "inline"})

    @app.post("/api/v1/claims/{cid}/input-revisions", status_code=202)
    def commit_input(cid: str, body: InputCreate, request: Request):
        with database.session.begin() as db:
            c = claim_for(db, cid, lock=True)
            def action():
                if len(body.file_ids) != len(set(body.file_ids)):
                    fail(422, "duplicate_file_ids", "Each file may appear only once.")
                records = [get(db, "file:" + fid) for fid in body.file_ids]
                if any(not f or f["claim_id"] != cid for f in records):
                    fail(409, "unknown_file", "Every file must belong to this claim.")
                photos = sum(f["role"] == "photograph" for f in records)
                pages = sum(f["page_count"] for f in records if f["role"] == "estimate_page")
                if photos > 40 or pages > 10:
                    fail(422, "input_limit_exceeded", "An input supports 40 photographs and 10 estimate pages.")
                if db.query(Outbox).filter(Outbox.published == 0).count() >= 1000:
                    fail(503, "dispatch_backlog", "Processing backlog is full. Please retry later.")
                c["input_revision"] += 1
                rev = c["input_revision"]
                data = {"claim_id": cid, "input_revision": rev, "file_ids": body.file_ids, "has_documents": pages > 0, "created_at": now(), "corrections": [], "source_kind": "fixture"}
                put(db, f"input:{cid}:{rev}", "input", data, cid)
                for item in records:
                    put(db, "file:" + item["file_id"], "file", {**item, "state": "committed"}, cid)
                c.update(status="processing", photograph_count=photos, assessment_revision=None, finding_count=0, estimate_row_count=0, declared_total=None)
                put(db, "claim:" + cid, "claim", c, cid)
                job = enqueue(db, cid, rev)
                return {"input_revision": rev, "job_keys": [job], "state": "queued"}
            return idem(db, request, body.model_dump(), action)

    @app.get("/api/v1/claims/{cid}/input-revisions/{rev}")
    def input_revision(cid: str, rev: int):
        with database.session() as db:
            claim_for(db, cid)
            result = get(db, f"input:{cid}:{rev}")
            if result is None:
                fail(404, "input_not_found", "Input revision not found.")
            return result

    @app.get("/api/v1/claims/{cid}/processing")
    @app.get("/api/v1/claims/{cid}/jobs")
    def processing(cid: str):
        with database.session() as db:
            c = claim_for(db, cid)
            jobs = [r.data for r in db.scalars(select(Record).where(Record.kind == "job", Record.claim_id == cid)) if r.data["input_revision"] == c["input_revision"]]
            return {"state": c["status"], "input_revision": c["input_revision"], "jobs": jobs, "stages": jobs, "dispatch_pending": any(j["state"] == "pending" for j in jobs), "fixture_mode": True}

    @app.get("/api/v1/claims/{cid}/assessments")
    def assessments(cid: str):
        with database.session() as db:
            claim_for(db, cid)
            return {"items": [r.data for r in db.scalars(select(Record).where(Record.kind == "assessment", Record.claim_id == cid))]}

    @app.get("/api/v1/claims/{cid}/assessments/{rev}")
    def assessment_detail(cid: str, rev: int):
        with database.session() as db:
            c = claim_for(db, cid)
            a = assessment_for(db, cid, rev)
            review = get(db, f"review:{cid}:{rev}")
            a["review_revision"] = review["review_revision"]
            inp = get(db, f"input:{cid}:{a['input_revision']}")
            a["files"] = [get(db, "file:" + fid) for fid in inp["file_ids"]]
            a["finalize_preconditions"] = preconditions(c, a, review)
            return a

    @app.get("/api/v1/claims/{cid}/assessments/{rev}/review")
    def review_detail(cid: str, rev: int):
        with database.session() as db:
            claim_for(db, cid)
            assessment_for(db, cid, rev)
            return get(db, f"review:{cid}:{rev}")

    def review_context(db, cid, rev, expected):
        c = claim_for(db, cid, lock=True)
        a = assessment_for(db, cid, rev)
        review = deepcopy(get(db, f"review:{cid}:{rev}"))
        if c["review_revision"] != expected or c["assessment_revision"] != rev or c["input_revision"] != a["input_revision"]:
            fail(409, "stale_revision", "Reload the current assessment before saving.", current_revision=c["review_revision"], submitted_revision=expected)
        if review["finalized"]:
            fail(409, "review_finalized", "This review is frozen.")
        return c, a, review

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/notes")
    def note(cid: str, rev: int, body: Note, request: Request):
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)
            def action():
                c, a, review = review_context(db, cid, rev, body.expected_review_revision)
                c["review_revision"] += 1
                event = {"action_id": uid(), "type": "note", "text": body.text, "actor": DEMO_USER["name"], "created_at": now()}
                review["actions"].append(event)
                review["review_revision"] = c["review_revision"]
                put(db, f"review:{cid}:{rev}", "review", review, cid)
                put(db, "claim:" + cid, "claim", c, cid)
                return {"review_revision": c["review_revision"], "action_id": event["action_id"]}
            return idem(db, request, body.model_dump(), action)

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/marks/{mark_id}/decision", status_code=202)
    def mark_decision(cid: str, rev: int, mark_id: str, body: MarkDecision, request: Request):
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)
            def action():
                c, a, review = review_context(db, cid, rev, body.expected_review_revision)
                if body.expected_input_revision != c["input_revision"]:
                    fail(409, "stale_input_revision", "Input revision changed.")
                mark = next((m for m in a["marks"] if m["mark_id"] == mark_id), None)
                if not mark:
                    fail(404, "mark_not_found", "Mark not found.")
                if mark["state"] != "pending":
                    fail(422, "mark_already_resolved", "Mark already resolved.")
                amount = None
                if body.decision == "confirm":
                    try:
                        value = Decimal(body.amount or "NaN")
                        if not value.is_finite() or value < 0 or value > 999999999 or value.as_tuple().exponent < -2:
                            raise InvalidOperation
                        amount = format(value, ".2f")
                    except InvalidOperation:
                        fail(422, "amount_required", "Enter a nonnegative decimal amount with at most two decimal places.")
                event = {"action_id": uid(), "type": "mark_decision", "mark_id": mark_id, "decision": body.decision, "amount": amount, "actor": DEMO_USER["name"], "created_at": now()}
                old_input = get(db, f"input:{cid}:{c['input_revision']}")
                c["input_revision"] += 1
                c["review_revision"] += 1
                new_input = {**deepcopy(old_input), "input_revision": c["input_revision"], "base_assessment_revision": rev, "corrections": [event], "created_at": now()}
                put(db, f"input:{cid}:{c['input_revision']}", "input", new_input, cid)
                review["actions"].append(event)
                review["review_revision"] = c["review_revision"]
                put(db, f"review:{cid}:{rev}", "review", review, cid)
                c.update(status="processing", assessment_revision=None)
                put(db, "claim:" + cid, "claim", c, cid)
                job = enqueue(db, cid, c["input_revision"])
                return {"review_revision": c["review_revision"], "input_revision": c["input_revision"], "assessment_revision": None, "state": "queued", "job_keys": [job]}
            return idem(db, request, body.model_dump(), action)

    @app.get("/api/v1/claims/{cid}/assessments/{rev}/finalize-preconditions")
    def finalize_preconditions(cid: str, rev: int):
        with database.session() as db:
            c = claim_for(db, cid)
            a = assessment_for(db, cid, rev)
            return preconditions(c, a, get(db, f"review:{cid}:{rev}"))

    @app.post("/api/v1/claims/{cid}/assessments/{rev}/finalize")
    def finalize(cid: str, rev: int, body: Revision, request: Request):
        with database.session.begin() as db:
            claim_for(db, cid, lock=True)
            def action():
                c, a, review = review_context(db, cid, rev, body.expected_review_revision)
                checks = preconditions(c, a, review)
                if not checks["can_finalize"]:
                    fail(412, "finalize_blocked", "Resolve the remaining review requirements.", preconditions=checks)
                c["status"] = "ready_to_print"
                c["review_revision"] += 1
                review["review_revision"] = c["review_revision"]
                review.update(finalized=True, finalized_at=now(), claim_snapshot=deepcopy(c))
                put(db, f"review:{cid}:{rev}", "review", review, cid)
                put(db, "claim:" + cid, "claim", c, cid)
                return {"review_revision": review["review_revision"], "assessment_revision": rev, "print_view_url": f"/api/v1/claims/{cid}/assessments/{rev}/print-view?review_revision={review['review_revision']}"}
            return idem(db, request, body.model_dump(), action)

    @app.get("/api/v1/claims/{cid}/assessments/{rev}/print-view")
    def print_view(cid: str, rev: int, review_revision: int):
        with database.session() as db:
            claim_for(db, cid)
            a = assessment_for(db, cid, rev)
            review = get(db, f"review:{cid}:{rev}")
            if not review["finalized"]:
                fail(412, "not_finalized", "Finalize this review before printing.")
            if review_revision != review["review_revision"]:
                fail(409, "review_revision_mismatch", "The requested frozen review does not match.")
            return {"claim": review["claim_snapshot"], "assessment": a, "review": review, "disclaimer": "Demonstration fixtures only. Not model results, real price validation or final claim approval."}

    return app


app = create_app()
