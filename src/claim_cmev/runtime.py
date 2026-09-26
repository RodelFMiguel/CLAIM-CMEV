"""Configuration, the database handle and the generic claim/review record store.

Claims, files, inputs, reviews and sessions stay in ``baseline_records`` for this pass;
jobs, transport state, stage results and assessments use the migrated tables in
``claim_cmev.persistence.tables``. The schema is created by Alembic, never by
``create_all``.
"""
import hashlib
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .persistence.tables import SQLITE_SCHEMA_MAP


def setting(name, default="", alias=None):
    return os.getenv("CMEV_" + name, os.getenv(alias or name, default))


def now():
    return datetime.now(timezone.utc).isoformat()


def utcnow():
    return datetime.now(timezone.utc)


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


def _sqlite_foreign_keys(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    def __init__(self, url=None):
        url = url or setting("DATABASE_URL", "sqlite:///./runtime/baseline.db")
        sqlite = url.startswith("sqlite")
        if sqlite:
            Path("runtime").mkdir(exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {}, pool_pre_ping=True)
        if sqlite:
            event.listen(engine, "connect", _sqlite_foreign_keys)
            engine = engine.execution_options(schema_translate_map=SQLITE_SCHEMA_MAP)
        self.url = url
        self.engine = engine
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_postgres(self):
        return self.engine.dialect.name == "postgresql"

    def initialize(self):
        """Upgrade to the Alembic head under the migration advisory lock."""
        from .persistence.migrations import upgrade
        return upgrade(self.engine)


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
