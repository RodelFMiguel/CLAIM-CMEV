"""M4 runtime integration on synthetic pixels and explicit fixture OCR, never accuracy evidence."""
import hashlib
import io

from PIL import Image
import pytest
from sqlalchemy import select

from claim_cmev.documents.text_layout.config import load_page_reading_config
from claim_cmev.documents.text_layout.ocr import StubOcrEngine, OcrRegion, OcrEngineError
from claim_cmev.documents.text_layout.worker import make_page_read_handler
from claim_cmev.messaging.consumer import ConsumerRuntime
from claim_cmev.orchestration.consolidation import Consolidator
from claim_cmev.orchestration.intake import commit_input_revision
from claim_cmev.orchestration.plan import VersionBundle
from claim_cmev.orchestration.services import LocalPipeline, build_runtimes
from claim_cmev.persistence.tables import stage_records, jobs
from claim_cmev.runtime import put, uid
from claim_cmev.storage import Storage
from pipeline_helpers import Clock, table_version

TOPIC = 'cmev.cmd.page-read.v1'
GROUP = 'cmev-worker-ocr'


@pytest.fixture(scope='module')
def cost_tables(tmp_path_factory):
    # M4 runtime wiring needs a table version, not the optional LightGBM runtime.
    from claim_cmev.costs.reference.build import main
    root = tmp_path_factory.mktemp('ocr-costs')
    assert main(['--seed', '20260924', '--out', str(root), '--promote',
                 '--method', 'empirical_percentile']) == 0
    return root


def _setup(database, cost_tables, tmp_path, engine, *, pdf=False, bad_hash=False):
    cfg = load_page_reading_config()
    versions = VersionBundle.fixture()
    pins = {'ocr': 'stub-ocr/0.0.0', 'preprocess_config': cfg.config_version}
    versions = VersionBundle(stages={**versions.stages, 'page_read': pins}, intake=versions.intake)
    storage = Storage(tmp_path / 'evidence')
    buffer = io.BytesIO()
    im = Image.new('RGB', (600, 800), 'white')
    im.save(buffer, format='PDF' if pdf else 'PNG', **({'save_all': True, 'append_images': [im]} if pdf else {}))
    data = buffer.getvalue()
    storage.write('original/document', data, 'application/pdf' if pdf else 'image/png')
    cid, fid = uid(), uid()
    with database.session.begin() as db:
        claim = {'claim_id': cid, 'reference': cid, 'vehicle': {}, 'currency': 'SGD',
                 'input_revision': 0, 'review_revision': 0, 'status': 'awaiting_upload'}
        put(db, 'claim:' + cid, 'claim', claim, cid)
        file = {'file_id': fid, 'role': 'estimate_page', 'media_type': 'application/pdf' if pdf else 'image/png',
                'object_uri': storage.uri('original/document'), 'sha256': '0'*64 if bad_hash else hashlib.sha256(data).hexdigest(),
                'byte_count': len(data), 'width': 600, 'height': 800, 'page_count': 2 if pdf else 1,
                'exif_orientation': None}
        put(db, 'file:' + fid, 'file', file, cid)
        commit_input_revision(db, claim, [file], now=Clock()(), versions=versions,
                              cost_table_version=table_version(cost_tables), profile='lean', source_kind='fixture')
    handler = make_page_read_handler(engine, storage, pins, cfg)
    orchestrator = build_runtimes(database.session, 'orchestrator', versions=versions, consolidator=Consolidator(cost_table_root=cost_tables), profile='lean', clock=Clock())
    runtime = ConsumerRuntime(database.session, GROUP, {TOPIC: handler}, service=GROUP,
                              profile='lean', source_kind='fixture', clock=Clock(), sleep=lambda _: None)
    pipeline = LocalPipeline(database.session, [*orchestrator, runtime], clock=Clock())
    return pipeline, cid, storage


def _engine(**kwargs):
    return StubOcrEngine([OcrRegion(((20,20),(130,20),(130,45),(20,45)), 'BUMPER', .92, 'line')], **kwargs)


@pytest.mark.parametrize('pdf,count', [(False, 1), (True, 2)])
def test_pages_persist_artifacts_boxes_and_events_without_rereading_on_replay(database, cost_tables, tmp_path, pdf, count):
    engine = _engine()
    pipeline, cid, storage = _setup(database, cost_tables, tmp_path, engine, pdf=pdf)
    pipeline.drain()
    with database.session() as db:
        rows = db.execute(select(stage_records).where(stage_records.c.claim_id == cid)).mappings().all()
        tasks = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.stage == 'page_read')).mappings().all()
    assert len(tasks) == count and all(j['state'] == 'succeeded' for j in tasks)
    pages = [r['body'] for r in rows if r['record_kind'] == 'document_page']
    assert {p['page_number'] for p in pages} == set(range(1, count+1))
    assert len([r for r in rows if r['record_kind'] == 'page_text_box']) == count
    assert len([r for r in rows if r['record_kind'] == 'page_reading']) == count
    for page in pages:
        assert page['provenance']['source_kind'] == 'fixture'
        ref = page['page_reading_ref']
        assert hashlib.sha256(storage.read(ref['object_uri'].split('file://local-evidence/')[1])).hexdigest() == ref['sha256']
    for command in pipeline.broker.messages(TOPIC):
        assert pipeline.runtime(GROUP).process(command) == 'duplicate'
    pipeline.drain()
    assert engine.calls == count


@pytest.mark.parametrize('engine,bad_hash,code', [
    (StubOcrEngine(), False, 'ocr_no_text'),
    (_engine(error=OcrEngineError('ocr_failed', 'dependency failure')), False, 'ocr_failed'),
    (_engine(), True, 'artifact_hash_mismatch'),
])
def test_unreadable_or_corrupt_upload_never_succeeds(database, cost_tables, tmp_path, engine, bad_hash, code):
    pipeline, cid, _ = _setup(database, cost_tables, tmp_path, engine, bad_hash=bad_hash)
    pipeline.drain()
    with database.session() as db:
        task = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.stage == 'page_read')).mappings().one()
        assert task['state'] == 'dead_lettered' and task['reason_code'] == code
        assert not db.execute(select(stage_records).where(stage_records.c.claim_id == cid)).first()


def test_same_uploaded_page_new_revision_preserves_historical_records(database, cost_tables, tmp_path):
    from claim_cmev.runtime import get
    engine = _engine()
    pipeline, cid, _ = _setup(database, cost_tables, tmp_path, engine)
    pipeline.drain()
    with database.session.begin() as db:
        first = db.execute(select(stage_records.c.body).where(stage_records.c.claim_id == cid,
                            stage_records.c.record_kind == 'document_page')).scalar_one()
        claim = get(db, 'claim:' + cid)
        inp = get(db, f'input:{cid}:1')
        file = get(db, 'file:' + inp['file_ids'][0])
        bundle = VersionBundle.fixture()
        versions = VersionBundle(stages={**bundle.stages, 'page_read': {
            'ocr': 'stub-ocr/0.0.0', 'preprocess_config': load_page_reading_config().config_version}})
        commit_input_revision(db, claim, [file], now=Clock()(), versions=versions,
                              cost_table_version=table_version(cost_tables), profile='lean', source_kind='fixture')
    pipeline.drain()
    with database.session() as db:
        pages = list(db.execute(select(stage_records.c.body).where(stage_records.c.claim_id == cid,
                     stage_records.c.record_kind == 'document_page').order_by(stage_records.c.input_revision)).scalars())
    assert len(pages) == 2
    assert pages[0] == first
    assert pages[0]['page_id'] == pages[1]['page_id']
    assert [p['input_revision'] for p in pages] == [1, 2]
    assert engine.calls == 2


def test_obsolete_queued_page_is_skipped_before_storage_or_ocr(database, cost_tables, tmp_path):
    from claim_cmev.runtime import get
    engine = _engine()
    pipeline, cid, storage = _setup(database, cost_tables, tmp_path, engine)
    with database.session.begin() as db:
        claim = get(db, 'claim:' + cid)
        inp = get(db, f'input:{cid}:1')
        file = get(db, 'file:' + inp['file_ids'][0])
        bundle = VersionBundle.fixture()
        versions = VersionBundle(stages={**bundle.stages, 'page_read': {
            'ocr': 'stub-ocr/0.0.0', 'preprocess_config': load_page_reading_config().config_version}})
        commit_input_revision(db, claim, [file], now=Clock()(), versions=versions,
                              cost_table_version=table_version(cost_tables), profile='lean', source_kind='fixture')
    pipeline.relay()
    # Deliver only old intake to enqueue old page while revision 2 already exists.
    from claim_cmev.orchestration.plan import INPUT_TOPIC
    old_input = next(m for m in pipeline.broker.messages(INPUT_TOPIC) if m.value['input_revision'] == 1)
    pipeline.runtime('cmev-orchestrator').process(old_input)
    pipeline.relay()
    old_command = next(m for m in pipeline.broker.messages(TOPIC) if m.value['input_revision'] == 1)
    storage.read = lambda _key: pytest.fail('obsolete OCR must not read object bytes')
    assert pipeline.runtime(GROUP).process(old_command) == 'dead_lettered'
    assert engine.calls == 0
    assert not pipeline.broker.messages('cmev.evt.page-read.v1')
    with database.session() as db:
        task = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.stage == 'page_read',
                          jobs.c.input_revision == 1)).mappings().one()
        assert task['reason_code'] == 'superseded_input_revision'
