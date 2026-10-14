"""M4 → neural M5 → M6 → M8 contract integration with deterministic fixture inference."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction
from claim_cmev.documents.line_items.config import load_layout_families
from claim_cmev.documents.line_items.vocabulary import load_estimate_vocabulary
from claim_cmev.documents.line_items.worker import make_line_items_handler
from claim_cmev.documents.pen_marks.worker import make_pen_marks_handler
from claim_cmev.documents.pen_marks.linking import Detection
from claim_cmev.documents.pen_marks.config import load_linking_config
from claim_cmev.documents.model_config import load_document_model_config
from claim_cmev.documents.text_layout.ocr import StubOcrEngine, OcrRegion
from claim_cmev.messaging.consumer import ConsumerRuntime
from claim_cmev.orchestration.plan import VersionBundle, COMMAND_TOPIC, SERVICE
from claim_cmev.orchestration.services import LocalPipeline, build_runtimes
from claim_cmev.orchestration.consolidation import Consolidator
from claim_cmev.persistence.tables import stage_records, jobs, assessments
from pipeline_helpers import Clock
from test_ocr_worker import _setup, cost_tables


class Labels:
    def __init__(self): self.calls = 0
    def predict(self, page, path):
        self.calls += 1
        labels = {'BUMPER': 'B-part_1_description', 'REPLACE': 'B-part_1_action', '980.00': 'B-part_1_amount'}
        return tuple(WordPrediction(b.box_id, labels.get(b.text, 'O'), .99) for b in page.text_boxes)


class Marks:
    def __init__(self): self.calls = 0
    def detect(self, image, *, page_id):
        self.calls += 1
        h, w = image.shape[:2]
        return [Detection(page_id=page_id, box_norm=(450/w,15/h,575/w,50/h), mark_type='price_change', score=.99)]


def document_pipeline(database, cost_tables, tmp_path, *, pdf=False):
    engine = StubOcrEngine([
        OcrRegion(((20,20),(130,20),(130,45),(20,45)), 'BUMPER', .99, 'line'),
        OcrRegion(((240,20),(350,20),(350,45),(240,45)), 'REPLACE', .99, 'line'),
        OcrRegion(((460,20),(570,20),(570,45),(460,45)), '980.00', .99, 'line')])
    initial, cid, storage = _setup(database, cost_tables, tmp_path, engine, pdf=pdf)
    from claim_cmev.runtime import get, put
    with database.session.begin() as db:
        claim=get(db,'claim:'+cid);claim['owner_id']='demo-surveyor';put(db,'claim:'+cid,'claim',claim,cid)
    base = VersionBundle.fixture()
    m5, m6 = load_document_model_config('lineitems'), load_document_model_config('penmarks')
    pins5, pins6 = m5.stage_versions('0.2.0'), m6.stage_versions('0.2.0')
    versions = VersionBundle(stages={**base.stages, 'page_read': {'ocr':'stub-ocr/0.0.0',
        'preprocess_config':'m4-page-reading/0.1.0'}, 'line_items':pins5, 'pen_marks':pins6})
    models = Labels(), Marks()
    handlers = [make_line_items_handler(models[0], storage, pins5, m5, load_layout_families(), load_estimate_vocabulary()),
                make_pen_marks_handler(models[1], storage, pins6, m6, load_linking_config())]
    runtimes = build_runtimes(database.session, 'orchestrator', versions=versions,
        consolidator=Consolidator(cost_table_root=cost_tables), profile='full', clock=Clock())
    runtimes += [initial.runtime('cmev-worker-ocr')]
    for stage, handler in zip(['line_items','pen_marks'], handlers):
        runtimes.append(ConsumerRuntime(database.session, SERVICE[stage], {COMMAND_TOPIC[stage]:handler},
            service=SERVICE[stage], profile='full', source_kind='fixture', clock=Clock(), sleep=lambda _:None))
    runtimes += build_runtimes(database.session, 'consolidator', versions=versions,
        consolidator=Consolidator(cost_table_root=cost_tables), profile='full', clock=Clock())
    return LocalPipeline(database.session, runtimes, broker=initial.broker, clock=Clock()), cid, storage, models


@pytest.mark.parametrize('pdf,count', [(False,1),(True,2)])
def test_document_chain_persists_pending_marks_partial_scope_and_replays(database, cost_tables, tmp_path, pdf, count):
    pipeline,cid,storage,models = document_pipeline(database,cost_tables,tmp_path,pdf=pdf)
    pipeline.drain()
    with database.session() as db:
        tasks = db.execute(select(jobs).where(jobs.c.claim_id==cid)).mappings().all()
        assert all(j['state']=='succeeded' for j in tasks), [(j['stage'],j['state'],j['reason_code']) for j in tasks]
        records=db.execute(select(stage_records).where(stage_records.c.claim_id==cid)).mappings().all()
        items=[r['body'] for r in records if r['record_kind']=='line_item']
        marks=[r['body'] for r in records if r['record_kind']=='pen_mark']
        assert len(items)==count and len(marks)==count
        assert all(m['state']=='pending' and m['confirmed_amount'] is None for m in marks)
        assert all(m['entry_id'] in {i['entry_id'] for i in items} for m in marks)
        assert all(i['printed_line_amount']=='980.00' for i in items)
        declaration=next(r['body'] for r in records if r['record_kind']=='declaration_completeness')
        assert declaration['state']=='partial'
        for j in tasks:
            for k in ('extraction_ref','detection_ref'):
                if k in (j['result_ref'] or {}):
                    ref=j['result_ref'][k]
                    assert storage.read(ref['object_uri'].removeprefix(storage.uri('')))
        assessment=db.execute(select(assessments).where(assessments.c.claim_id==cid)).mappings().one()
        assert all(i['effective_price'] is None for i in assessment['inputs']['line_items'])
    for stage in ['line_items','pen_marks']:
        for command in pipeline.broker.messages(COMMAND_TOPIC[stage]):
            assert pipeline.runtime(SERVICE[stage]).process(command)=='duplicate'
    assert [m.calls for m in models]==[count,count]


def queued_stage(pipeline, stage):
    pipeline.consumers = [(rt,c) for rt,c in pipeline.consumers if rt.group != SERVICE[stage]]
    pipeline.drain()
    return pipeline.broker.messages(COMMAND_TOPIC[stage])[0]


@pytest.mark.parametrize('stage', ['line_items','pen_marks'])
@pytest.mark.parametrize('failure', ['corrupt_image','foreign_page','stale','wrong_model'])
def test_bad_inputs_never_produce_success(database,cost_tables,tmp_path,stage,failure):
    from copy import deepcopy
    from dataclasses import replace
    from claim_cmev.runtime import get, put
    pipeline,cid,storage,models=document_pipeline(database,cost_tables,tmp_path)
    command=queued_stage(pipeline,stage)
    body=deepcopy(command.value)
    if failure=='corrupt_image':
        key=body['payload']['pages'][0]['corrected_render_ref']['object_uri'].removeprefix(storage.uri(''))
        storage.write(key,b'corrupt','image/png')
    elif failure=='foreign_page':
        body['payload']['pages'][0]['page_id']='foreign-page'
    elif failure=='wrong_model':
        body['payload']['model_version']='another-model'
    else:
        with database.session.begin() as db:
            from claim_cmev.orchestration.intake import commit_input_revision
            from pipeline_helpers import table_version
            claim=get(db,'claim:'+cid)
            inp=get(db,f'input:{cid}:1')
            files=[get(db,'file:'+fid) for fid in inp['file_ids']]
            commit_input_revision(db,claim,files,now=Clock()(),versions=VersionBundle.fixture(),
                cost_table_version=table_version(cost_tables),profile='full',source_kind='fixture')
    assert pipeline.runtime(SERVICE[stage]).process(replace(command,value=body))=='dead_lettered'
    pipeline.relay()
    assert not pipeline.broker.messages('cmev.evt.' + ('line-items-extracted' if stage=='line_items' else 'pen-marks-detected') + '.v1')
    assert models[0 if stage=='line_items' else 1].calls==0
    with database.session() as db:
        assert not db.execute(select(stage_records).where(stage_records.c.claim_id==cid,stage_records.c.stage==stage)).first()


def test_m6_rejects_substituted_full_band_rows(database,cost_tables,tmp_path):
    from copy import deepcopy
    from dataclasses import replace
    pipeline,cid,storage,models=document_pipeline(database,cost_tables,tmp_path)
    command=queued_stage(pipeline,'pen_marks');body=deepcopy(command.value)
    body['payload']['row_boxes'][0]['row_box_norm']=[0,0,1,1]
    assert pipeline.runtime(SERVICE['pen_marks']).process(replace(command,value=body))=='dead_lettered'
    assert models[1].calls==0


def test_artifact_write_failure_retries_without_duplicate_inference_records(database,cost_tables,tmp_path):
    pipeline,cid,storage,models=document_pipeline(database,cost_tables,tmp_path)
    command=queued_stage(pipeline,'line_items')
    write=storage.write
    attempts=[]
    def unreliable(*args):
        attempts.append(1)
        if len(attempts)==1: raise OSError('temporary storage outage')
        return write(*args)
    storage.write=unreliable
    assert pipeline.runtime(SERVICE['line_items']).process(command)=='processed'
    assert len(attempts)==2
    with database.session() as db:
        assert len(db.execute(select(stage_records).where(stage_records.c.claim_id==cid,stage_records.c.record_kind=='line_item')).all())==1


def test_corrected_page_api_is_assessment_scoped_and_integrity_checked(database,cost_tables,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import update
    from claim_cmev.api.main import create_app
    pipeline,cid,storage,models=document_pipeline(database,cost_tables,tmp_path)
    pipeline.drain()
    # Explicit synthetic API evidence fixture, with the bytes produced by the M4 test adapter.
    with database.session.begin() as db:
        assessment=db.execute(select(assessments).where(assessments.c.claim_id==cid)).mappings().one()
        inputs=assessment['inputs'];page=inputs['pages'][0]

    monkeypatch.setenv('CMEV_SEED','false')
    app=create_app(str(database.engine.url),str(storage.directory))
    route=f"/api/v1/claims/{cid}/assessments/1/pages/{page['page_id']}/corrected"
    with TestClient(app) as client:
        assert client.get(route).status_code==401
        assert client.post('/api/v1/auth/login',json={'email':'surveyor@claim-cmev.demo','password':'Demo2026!'}).status_code==200
        response=client.get(route)
        assert response.status_code==200 and response.headers['content-type']=='image/png', (response.text, page, assessment['assessment_revision'])
        assert client.get(route.replace(page['page_id'],'foreign-page')).status_code==404
        ref=page['corrected_render_ref']
        storage.write(ref['object_uri'].removeprefix(storage.uri('')),b'changed','image/png')
        assert client.get(route).status_code==409
