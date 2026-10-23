"""The consolidator's write of an extracted surveyor name never undoes other claim changes.

SQLite and the in-memory transport. The concurrent API commit is simulated in the same
session with a Core UPDATE, which bypasses the ORM identity map exactly as a row committed
by another transaction would.
"""
from __future__ import annotations

from sqlalchemy import select, update

from claim_cmev.orchestration import consolidation
from claim_cmev.orchestration.consolidation import Consolidator, record_extracted_surveyor
from claim_cmev.orchestration.plan import VersionBundle
from claim_cmev.orchestration.services import LocalPipeline, build_runtimes
from claim_cmev.persistence.tables import assessments
from claim_cmev.runtime import Database, Record, get, put
from pipeline_helpers import Clock, make_claim

CLAIM = "01K6F1XTVRE00000000000S001"


def database(tmp_path, name="claims.db"):
    db = Database("sqlite:///" + str(tmp_path / name))
    db.initialize()
    return db


def test_surveyor_write_keeps_a_claim_change_committed_after_the_claim_was_read(tmp_path):
    store = database(tmp_path)
    with store.session.begin() as db:
        put(db, "claim:" + CLAIM, "claim", {"claim_id": CLAIM, "input_revision": 1, "surveyor": "Acery AI"}, CLAIM)
    with store.session.begin() as db:
        held = db.get(Record, "claim:" + CLAIM)  # the consolidator's earlier read, still in the identity map
        assert held.data["input_revision"] == 1
        db.execute(update(Record.__table__).where(Record.__table__.c.key == "claim:" + CLAIM).values(
            data={"claim_id": CLAIM, "input_revision": 2, "surveyor": "Acery AI"}))  # the API moved the claim on
        record_extracted_surveyor(db, CLAIM, "H. Tan")
    with store.session() as db:
        assert get(db, "claim:" + CLAIM) == {"claim_id": CLAIM, "input_revision": 2, "surveyor": "H. Tan"}


def test_a_superseded_consolidation_does_not_write_the_claim(tmp_path, cost_tables, monkeypatch):
    monkeypatch.setattr(consolidation, "extract_surveyor_from_pages", lambda _pages: "H. Tan")
    store = database(tmp_path)
    runtimes = build_runtimes(store.session, "combined", versions=VersionBundle.fixture(),
                              consolidator=Consolidator(cost_table_root=cost_tables), profile="lean",
                              clock=Clock(), sleep=lambda _s: None)
    pipeline = LocalPipeline(store.session, runtimes, clock=Clock())
    cid = make_claim(store, "exclusion_and_supported", cost_tables, claim_id=CLAIM, clock=Clock())
    with store.session.begin() as db:  # the API committed input 2 before input 1 was consolidated
        row = db.get(Record, "claim:" + cid)
        row.data = {**row.data, "input_revision": 2, "surveyor": "Acery AI"}
    pipeline.drain()
    with store.session() as db:
        superseded = db.execute(select(assessments.c.superseded).where(
            assessments.c.claim_id == cid, assessments.c.input_revision == 1)).scalar_one()
        claim = get(db, "claim:" + cid)
    assert superseded is True  # input 1 was consolidated, as a superseded revision
    assert (claim["input_revision"], claim["surveyor"]) == (2, "Acery AI")
