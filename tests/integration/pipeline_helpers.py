"""Helpers for the runtime integration tests: claims, commits and selective stepping."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from claim_cmev.costs.reference import active_table_version
from claim_cmev.fixtures import _bundle, seed_files
from claim_cmev.messaging.consumer import consume_batch
from claim_cmev.orchestration.intake import commit_input_revision
from claim_cmev.orchestration.plan import VersionBundle
from claim_cmev.orchestration.services import LocalPipeline
from claim_cmev.runtime import Database, get, put, uid

FIXED = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
PRODUCER_GROUPS = ("cmev-worker-parts", "cmev-worker-damage", "cmev-worker-summary", "cmev-worker-ocr",
                   "cmev-worker-lineitems", "cmev-worker-penmarks")
IMAGE_GROUPS = ("cmev-worker-parts", "cmev-worker-damage", "cmev-worker-summary")
DOCUMENT_GROUPS = ("cmev-worker-ocr", "cmev-worker-lineitems", "cmev-worker-penmarks")
ALL_STAGES = ["damage", "line_items", "page_read", "parts", "pen_marks", "summary"]


class Clock:
    """Deterministic, advancing clock so repeated runs produce identical records."""

    def __init__(self, start: datetime = FIXED):
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


def table_version(cost_tables) -> str:
    return active_table_version(cost_tables)


def make_claim(database: Database, scenario: str, cost_tables, *, claim_id: str | None = None,
               photos: bool = True, pages: bool = True, clock=None) -> str:
    """A claim whose input revision 1 holds the scenario's fixture files (placeholders, no bytes)."""
    cid = claim_id or uid()
    with database.session.begin() as db:
        claim = {"claim_id": cid, "reference": f"IT-{cid[-6:]}", "owner_id": "demo-surveyor",
                 "vehicle": {"make": "Test", "model": "Car", "year": 2020,
                             "vehicle_class": _bundle(scenario, cid, 1).vehicle_class},
                 "currency": "SGD", "input_revision": 0, "review_revision": 0, "source_kind": "fixture",
                 "fixture_scenario": scenario, "status": "awaiting_upload"}
        put(db, "claim:" + cid, "claim", claim, cid)
        files = [f for f in seed_files(db, claim)
                 if (photos or f["role"] != "photograph") and (pages or f["role"] != "estimate_page")]
        commit_input_revision(db, claim, files, now=(clock or Clock())(), versions=VersionBundle.fixture(),
                              cost_table_version=table_version(cost_tables), profile="lean", source_kind="fixture")
    return cid


def commit_again(database: Database, cid: str, cost_tables, **options) -> int:
    """Commit the next input revision over the same files (optionally reusing earlier stages)."""
    with database.session.begin() as db:
        claim = dict(get(db, "claim:" + cid))
        previous = get(db, f"input:{cid}:{claim['input_revision']}")
        files = [get(db, "file:" + fid) for fid in previous["file_ids"]]
        result = commit_input_revision(db, claim, files, now=options.pop("now", FIXED), versions=VersionBundle.fixture(),
                                       cost_table_version=options.pop("cost_table_version", table_version(cost_tables)),
                                       profile="lean", source_kind="fixture", **options)
    return result["input_revision"]


def step(pipeline: LocalPipeline, groups: Iterable[str]) -> int:
    """Relay once, then give only the named consumer groups one batch each."""
    moved = pipeline.relay()
    wanted = set(groups)
    for runtime, consumer in pipeline.consumers:
        if runtime.group in wanted:
            moved += consume_batch(runtime, consumer)
    return moved


def settle(pipeline: LocalPipeline, groups: Iterable[str], rounds: int = 100) -> None:
    groups = list(groups)
    for _ in range(rounds):
        if not step(pipeline, groups):
            return
    raise AssertionError("pipeline did not settle")
