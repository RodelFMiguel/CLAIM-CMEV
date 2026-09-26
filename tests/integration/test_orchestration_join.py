"""Branch join, single consolidate emit, failures, operator retry, dead-letter replay and stale revisions.

Application platform sections 6 and 11, integration contracts section 11, evaluation
plan SVC-03, SVC-11, SVC-13 and SVC-16. SQLite and the in-memory transport only.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from claim_cmev.messaging.consumer import PermanentError
from claim_cmev.messaging.transport import DLQ_TOPIC
from claim_cmev.orchestration import state
from claim_cmev.orchestration.services import local_pipeline
from claim_cmev.persistence.tables import assessments, branch_state, current_pointer, jobs, outbox, stage_records
from claim_cmev.runtime import Database
from claim_cmev.api import views
from claim_cmev.runtime import get
from pipeline_helpers import DOCUMENT_GROUPS, IMAGE_GROUPS, Clock, commit_again, make_claim, settle, step

CLAIM = "01K6F1XTVRE00000000000A001"


def assessment_body(database, cid, revision=1):
    with database.session() as db:
        return db.execute(select(assessments.c.body).where(assessments.c.claim_id == cid,
                                                           assessments.c.assessment_revision == revision)).scalar()


def consolidate_commands(pipeline, cid):
    return [m for m in pipeline.broker.messages("cmev.cmd.consolidate.v1") if m.key == cid]


@pytest.mark.parametrize("first", [DOCUMENT_GROUPS, IMAGE_GROUPS])
def test_branches_complete_out_of_order_with_identical_results(tmp_path, cost_tables, first):
    bodies = []
    for order in (first, None):
        database = Database("sqlite:///" + str(tmp_path / f"{'-'.join(first)}-{bool(order)}.db"))
        database.initialize()
        pipeline = local_pipeline(database, clock=Clock(), sleep=lambda _s: None)
        make_claim(database, "exclusion_and_supported", cost_tables, claim_id=CLAIM, clock=Clock())
        if order:
            settle(pipeline, ["cmev-orchestrator", *order])  # one branch finishes completely first
            with database.session() as db:
                bs = state.branch_row(db, CLAIM, 1)
            finished = [s for s in ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")
                        if bs[s] == "done"]
            assert finished and not state.join_ready(bs) and bs["consolidate_emitted_at"] is None
            assert not consolidate_commands(pipeline, CLAIM)
        pipeline.drain()
        assert len(consolidate_commands(pipeline, CLAIM)) == 1
        body = assessment_body(database, CLAIM)
        bodies.append({k: v for k, v in body.items() if k != "created_at"})
    assert bodies[0] == bodies[1]  # same findings, IDs and pinned versions whatever the arrival order


def test_one_failed_branch_blocks_consolidation_and_keeps_the_other_readable(database, cost_tables, pipeline):
    cid = make_claim(database, "pending_price_change", cost_tables)
    ocr = pipeline.runtime("cmev-worker-ocr")
    ocr.handlers["cmev.cmd.page-read.v1"] = lambda ctx: (_ for _ in ()).throw(
        PermanentError("ocr_engine_error", "fixture OCR stand-in forced to fail"))
    pipeline.drain()
    with database.session() as db:
        bs = state.branch_row(db, cid, 1)
        claim = views.claim_view(db, get(db, "claim:" + cid))
        processing = views.processing_view(db, get(db, "claim:" + cid))
        image_records = db.execute(select(func.count()).select_from(stage_records).where(
            stage_records.c.claim_id == cid, stage_records.c.stage == "summary")).scalar()
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 0
        assert db.execute(select(func.count()).select_from(jobs).where(jobs.c.task == "consolidate")).scalar() == 0
    assert bs["page_read"] == "failed" and bs["summary"] == "done" and bs["consolidate_emitted_at"] is None
    assert state.branch_states(bs) == {"image": "complete", "document": "failed"}
    assert claim["status"] == "incomplete" and claim["assessment_revision"] is None
    assert processing["incomplete"] and processing["dead_lettered"]
    assert processing["failures"][0]["reason_code"] == "ocr_engine_error"
    assert image_records > 0  # the successful branch stays readable
    assert [m.value["payload"]["reason_code"] for m in pipeline.broker.messages("cmev.evt.job-failed.v1")] == \
        ["ocr_engine_error"]
    assert len(pipeline.broker.messages(DLQ_TOPIC)) == 1


def test_consolidate_emit_is_claimed_once_by_concurrent_orchestrators(database, cost_tables, pipeline):
    cid = make_claim(database, "unphotographed_part", cost_tables)
    pipeline.drain()
    with database.session.begin() as db:  # rewind the emit claim to the instant before the join fired
        db.execute(update(branch_state).where(branch_state.c.claim_id == cid).values(consolidate_emitted_at=None))
    replica_a, replica_b = database.session(), database.session()
    try:
        row_a, row_b = state.branch_row(replica_a, cid, 1), state.branch_row(replica_b, cid, 1)
        assert state.join_ready(row_a) and state.join_ready(row_b)  # both replicas see the join complete
        assert state.claim_consolidate_emit(replica_a, row_a["id"], Clock()()) is True
        replica_a.commit()
        assert state.claim_consolidate_emit(replica_b, row_b["id"], Clock()()) is False  # the loser commits nothing
        replica_b.commit()
    finally:
        replica_a.close()
        replica_b.close()
    assert len(consolidate_commands(pipeline, cid)) == 1


def test_stale_revision_never_moves_the_current_pointer(database, cost_tables, pipeline):
    cid = make_claim(database, "exclusion_and_supported", cost_tables)
    step(pipeline, ["cmev-orchestrator"])  # fan-out only: the parts commands wait in the outbox
    with database.session.begin() as db:  # a slow revision-1 parts worker: its commands are held back
        held_rows = db.execute(select(outbox).where(outbox.c.topic == "cmev.cmd.parts-segment.v1",
                                                    outbox.c.message_key == cid, outbox.c.published_at.is_(None))
                               ).mappings().all()
        db.execute(update(outbox).where(outbox.c.id.in_([r["id"] for r in held_rows]))
                   .values(published_at=held_rows[0]["created_at"]))
    assert held_rows
    pipeline.drain()
    assert commit_again(database, cid, cost_tables) == 2
    pipeline.drain()
    with database.session() as db:
        pointer = db.execute(select(current_pointer).where(current_pointer.c.claim_id == cid)).mappings().one()
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 1
    assert pointer["input_revision"] == 2
    current = pointer["assessment_revision"]
    for held in held_rows:  # the slow worker finally runs: late revision-1 parts-segmented events follow
        pipeline.broker.send(held["topic"], held["message_key"], held["payload"])
    pipeline.drain()
    assert any(m.value["input_revision"] == 1 for m in pipeline.broker.messages("cmev.evt.parts-segmented.v1")
               if m.key == cid)
    with database.session() as db:
        pointer = db.execute(select(current_pointer).where(current_pointer.c.claim_id == cid)).mappings().one()
        late = db.execute(select(assessments).where(assessments.c.claim_id == cid, assessments.c.input_revision == 1)
                          ).mappings().one()
        claim = views.claim_view(db, get(db, "claim:" + cid))
    assert (pointer["input_revision"], pointer["assessment_revision"]) == (2, current)
    assert late["superseded"] and late["body"]["superseded"] and late["assessment_revision"] > current
    assert claim["assessment_revision"] == current and claim["input_revision"] == 2


def test_operator_retry_replays_the_same_job_key_without_duplicates(database, cost_tables, pipeline):
    cid = make_claim(database, "partial_extraction", cost_tables)
    ocr = pipeline.runtime("cmev-worker-ocr")
    working = ocr.handlers["cmev.cmd.page-read.v1"]
    ocr.handlers["cmev.cmd.page-read.v1"] = lambda ctx: (_ for _ in ()).throw(PermanentError("ocr_engine_error"))
    pipeline.drain()
    with database.session() as db:
        failed = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.task == "page_read")).mappings().one()
        succeeded = db.execute(select(jobs.c.job_key).where(jobs.c.claim_id == cid, jobs.c.state == "succeeded",
                                                            jobs.c.task == "parts_segment")).scalars().first()
    assert failed["state"] == "dead_lettered"
    with database.session.begin() as db:
        with pytest.raises(state.JobConflict):
            state.retry_job(db, succeeded, now=Clock()())  # a succeeded job is never re-dispatched
    ocr.handlers["cmev.cmd.page-read.v1"] = working
    with database.session.begin() as db:
        retried = state.retry_job(db, failed["job_key"], now=Clock()())
    assert retried["job_key"] == failed["job_key"] and retried["attempt_epoch"] == 1 and retried["state"] == "pending"
    pipeline.drain()
    with database.session() as db:
        page_records = db.execute(select(stage_records.c.record_id).where(
            stage_records.c.job_key == failed["job_key"])).scalars().all()
        claim = views.claim_view(db, get(db, "claim:" + cid))
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 1
    assert len(page_records) == len(set(page_records)) == 1
    assert claim["status"] == "in_review"
    commands = [m for m in pipeline.broker.messages("cmev.cmd.page-read.v1") if m.key == cid]
    assert len({m.value["job_key"] for m in commands}) == 1 and len({m.value["dedup_key"] for m in commands}) == 2


def test_dead_letter_replay_is_refused_for_a_superseded_revision(database, cost_tables, pipeline):
    cid = make_claim(database, "partial_extraction", cost_tables)
    ocr = pipeline.runtime("cmev-worker-ocr")
    ocr.handlers["cmev.cmd.page-read.v1"] = lambda ctx: (_ for _ in ()).throw(PermanentError("ocr_engine_error"))
    pipeline.drain()
    with database.session() as db:
        failed = db.execute(select(jobs.c.job_key).where(jobs.c.claim_id == cid, jobs.c.task == "page_read")).scalar()
    commit_again(database, cid, cost_tables)
    with database.session.begin() as db:
        with pytest.raises(state.JobNotRetryable) as refused:
            state.retry_job(db, failed, now=Clock()())
    assert refused.value.reason_code == "revision_superseded"
    pipeline.drain()
    with database.session() as db:
        replayable = [m.value["payload"]["replayable"] for m in pipeline.broker.messages(DLQ_TOPIC)]
    assert replayable and replayable[0] is True  # it was replayable when dead-lettered, refused only now


def test_photos_only_and_pages_only_inputs_mark_stages_not_required(database, cost_tables, pipeline):
    photos_only = make_claim(database, "unphotographed_part", cost_tables, pages=False)
    pages_only = make_claim(database, "pending_price_change", cost_tables, photos=False)
    pipeline.drain()
    with database.session() as db:
        a = state.branch_row(db, photos_only, 1)
        b = state.branch_row(db, pages_only, 1)
        body_a = assessment_body(database, photos_only)
        body_b = assessment_body(database, pages_only)
        claim_a = views.claim_view(db, get(db, "claim:" + photos_only))
    assert [a[s] for s in ("page_read", "line_items", "pen_marks")] == ["not_required"] * 3
    assert [b[s] for s in ("parts", "damage", "summary")] == ["not_required"] * 3
    assert state.branch_states(a)["document"] == "skipped_no_pages"
    assert state.branch_states(b)["image"] == "skipped_no_photos"
    assert body_a["findings"] == [] and "document_branch_missing" in body_a["incomplete_reasons"]
    assert body_b["findings"] and "image_branch_missing" in body_b["incomplete_reasons"]
    assert all(f["photographic_check"]["result"] != "passed" for f in body_b["findings"])
    assert claim_a["processing_state"] == "awaiting_declared_entries"


def test_reuse_is_refused_when_a_pinned_version_differs(database, cost_tables, pipeline):
    cid = make_claim(database, "unphotographed_part", cost_tables)
    pipeline.drain()
    with database.session.begin() as db:  # pretend the parts stage ran with another model version
        db.execute(update(jobs).where(jobs.c.claim_id == cid, jobs.c.task == "parts_segment")
                   .values(versions={"parts_model": "other-parts/9.9.9"}))
    commit_again(database, cid, cost_tables, reuse_from=1, corrections=[])
    pipeline.drain()
    with database.session() as db:
        bs = state.branch_row(db, cid, 2)
        tasks = sorted(db.execute(select(jobs.c.task).where(jobs.c.claim_id == cid, jobs.c.input_revision == 2))
                       .scalars())
        lineage = [r["stage"] for r in state.lineage_records(db, cid, 2)]
    assert "reuse_refused_version_changed" in bs["reasons"]["parts"]
    assert "parts" not in lineage and "damage" not in lineage and "summary" not in lineage
    assert set(lineage) == {"page_read", "line_items", "pen_marks"}
    assert tasks.count("parts_segment") == 2 and "consolidate" in tasks
