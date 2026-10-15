"""Live document ownership and explicit model pins do not require model imports."""
import sys
import pytest
from claim_cmev.orchestration.services import RuntimeSettings, build_runtimes
from claim_cmev.orchestration.plan import VersionBundle, SERVICE
from claim_cmev.documents.model_config import load_document_model_config


@pytest.mark.parametrize('ocr,m5,m6',[('fixture','real','fixture'),('real','fixture','real'),('real','bad','fixture')])
def test_invalid_document_ownership_is_refused(monkeypatch,ocr,m5,m6):
    for name,value in [('OCR',ocr),('LINEITEMS',m5),('PENMARKS',m6)]:monkeypatch.setenv('CMEV_'+name+'_PRODUCER',value)
    with pytest.raises(ValueError):RuntimeSettings.from_env()
    with pytest.raises(ValueError):VersionBundle.for_runtime(ocr_producer=ocr,lineitems_producer=m5,penmarks_producer=m6)


def test_real_producers_relinquish_every_document_consumer():
    versions=VersionBundle.for_runtime(ocr_producer='real',lineitems_producer='real',penmarks_producer='real')
    runtimes=build_runtimes(None,'producers',profile='full',versions=versions,
                          ocr_producer='real',lineitems_producer='real',penmarks_producer='real')
    assert {r.group for r in runtimes}=={SERVICE[s] for s in ['parts','damage','summary']}
    assert versions.for_stage('line_items')['extraction_method']=='layoutlmv3'
    assert versions.for_stage('pen_marks')['penmark_model'].endswith('-serving')


def test_serving_config_pins_every_inference_file_and_rejects_corruption(tmp_path,monkeypatch):
    c=load_document_model_config('lineitems')
    root=tmp_path/c.model_version;root.mkdir(parents=True)
    monkeypatch.setenv('CMEV_MODEL_REGISTRY_PATH',str(tmp_path))
    (root/'manifest.json').write_text('{}')
    with pytest.raises(ValueError,match='pinned bytes'):c.verify()
    changed=c.model_copy(update={'min_confidence':0.7})
    assert c.sha256!=changed.sha256


def test_fully_real_pipeline_reports_actual_runtime_provenance(monkeypatch):
    for name in ['PARTS','DAMAGE','OCR','LINEITEMS','PENMARKS']:
        monkeypatch.setenv('CMEV_'+name+'_PRODUCER','real')
    assert RuntimeSettings.from_env().source_kind=='real'
