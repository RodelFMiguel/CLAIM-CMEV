"""Persistence and configuration for the explicitly fixture-backed baseline."""
import hashlib
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import JSON, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def setting(name, default="", alias=None):
    return os.getenv("CMEV_" + name, os.getenv(alias or name, default))


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(alphabet[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "baseline_records"
    key: Mapped[str] = mapped_column(String(250), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    claim_id: Mapped[str | None] = mapped_column(String(26), index=True, nullable=True)
    data: Mapped[dict] = mapped_column(JSON)


class Outbox(Base):
    __tablename__ = "baseline_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSON)
    published: Mapped[int] = mapped_column(Integer, default=0)


class Database:
    def __init__(self, url=None):
        url = url or setting("DATABASE_URL", "sqlite:///./runtime/baseline.db")
        if url.startswith("sqlite"):
            Path("runtime").mkdir(exist_ok=True)
        self.engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}, pool_pre_ping=True)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)


def put(db, key, kind, data, claim_id=None):
    row = db.get(Record, key)
    if row:
        row.data = data
    else:
        db.add(Record(key=key, kind=kind, claim_id=claim_id, data=data))
    return data


def get(db, key, default=None):
    row = db.get(Record, key)
    return row.data if row else default


def enqueue(db, claim_id, revision):
    topic = "cmev.evt.input-revision-created.v1"
    job_key = f"{claim_id}:{revision}:fixture-pipeline:fixture-v1"
    event = {"schema_version": "0.2.0", "topic": topic, "claim_id": claim_id, "input_revision": revision,
             "job_key": job_key, "dedup_key": digest(topic + "|" + job_key + "|0"), "created_at": now(),
             "producer_service": "cmev-api", "versions": {"fixture": "baseline-1"},
             "provenance": {"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "cmev-api"}}
    db.add(Outbox(payload=event))
    put(db, "job:" + job_key, "job", {"job_key": job_key, "input_revision": revision,
        "task": "fixture-pipeline", "state": "pending", "attempts": 0, "reason_code": "dispatch_pending", "source_kind": "fixture"}, claim_id)
    return job_key
