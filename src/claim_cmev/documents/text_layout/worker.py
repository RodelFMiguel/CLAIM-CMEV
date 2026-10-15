"""Persisted M4 consumer handler; delivery and retry semantics belong to ConsumerRuntime."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from pathlib import PurePosixPath

from claim_cmev.messaging.consumer import Context, PermanentError, TransientError
from claim_cmev.messaging.outbox import build_message
from claim_cmev.persistence.store import insert_records
from claim_cmev.storage import Storage
from claim_cmev.runtime import get
from claim_cmev.orchestration.intake import file_refs
from claim_cmev.orchestration.plan import page_target
from claim_cmev.orchestration.state import is_superseded
from claim_cmev.vision.artifacts import read_object

from .adapter import PageFileInput, PageReadRequest, run_page_reading, page_read_event_payload
from .config import PageReadingConfig
from .ocr import OcrEngine

PAGE_EVENT_TOPIC = 'cmev.evt.page-read.v1'


def make_page_read_handler(engine: OcrEngine, storage: Storage, versions: Mapping[str, str],
                           config: PageReadingConfig):
    """One command reads exactly one uploaded page, with the loaded version pins."""
    served = dict(versions)

    def handler(ctx: Context) -> dict[str, Any]:
        env, ref = ctx.envelope, ctx.payload['page']
        if config.ocr.skip_superseded_revisions and is_superseded(ctx.session, env.claim_id, env.input_revision):
            raise PermanentError('superseded_input_revision', 'a newer input revision supersedes this OCR request')
        if dict(env.versions) != served:
            raise PermanentError('model_version_unsupported', 'OCR command versions do not match this worker')
        if engine.info.source_kind == 'fixture' and ctx.source_kind != 'fixture':
            raise PermanentError('fixture_provenance_mismatch', 'fixture OCR requires fixture provenance')
        if (ctx.payload['ocr_engine'] != engine.info.engine or
                ctx.payload['ocr_version'] != engine.info.engine_version or
                ctx.payload['preprocess_config_version'] != config.config_version):
            raise PermanentError('version_mismatch', 'OCR payload pins differ from the loaded configuration')
        committed = get(ctx.session, f'input:{env.claim_id}:{env.input_revision}') or {}
        uploaded = get(ctx.session, 'file:' + ref['file_id'])
        if not uploaded or ref['file_id'] not in committed.get('file_ids', []):
            raise PermanentError('artifact_unlinked', 'estimate is not part of this input revision')
        if ref not in file_refs([uploaded])[1]:
            raise PermanentError('artifact_metadata_mismatch', 'estimate differs from the stored upload metadata')
        uri, prefix = ref['object_uri'], storage.uri('')
        if not uri.startswith(prefix):
            raise PermanentError('artifact_uri_invalid', 'estimate URI is outside this object store')
        key = uri[len(prefix):]
        if not key or '..' in PurePosixPath(key).parts or key.startswith('/'):
            raise PermanentError('artifact_uri_invalid', 'estimate key is invalid')
        number = ref.get('page_number') or 1
        if not ctx.job or ctx.job['target'] != page_target(ref['file_id'], number, ref['media_type'] == 'application/pdf'):
            raise PermanentError('page_identity_mismatch', 'command page differs from the job target')
        data = read_object(storage, uri, 'estimate upload')
        if len(data) != ref['byte_count']:
            raise PermanentError('artifact_size_mismatch', 'estimate byte count differs from the upload')
        result = run_page_reading(PageReadRequest(
            claim_id=env.claim_id, input_revision=env.input_revision, job_key=env.job_key,
            file=PageFileInput(file_id=ref['file_id'], media_type=ref['media_type'],
                               sha256=ref['sha256'], data=data, page_numbers=(number,)),
            versions=env.versions, provenance=ctx.provenance(), object_uri_prefix=prefix), engine, config)
        if result.processing_status == 'failed' or len(result.pages) != 1:
            reading = result.page_readings[0] if result.page_readings else None
            reason = reading['reasons'][0] if reading and reading['reasons'] else (
                result.reasons[0].model_dump() if result.reasons else
                {'code': 'page_unreadable', 'message': 'requested page could not be read'})
            error = TransientError if result.retryable else PermanentError
            raise error(reason['code'], reason['message'])
        page = result.pages[0]
        if page.page_number != number or page.file_id != ref['file_id']:
            raise PermanentError('page_identity_mismatch', 'OCR result differs from requested page')
        try:
            for artifact in result.artifacts:
                storage.write(artifact.key, artifact.data, artifact.media_type)
        except Exception as exc:
            raise TransientError('artifact_write_failed', 'could not persist OCR artifacts') from exc
        reading = result.page_readings[0]
        records = [('document_page', page), ('page_reading', reading)]
        records.extend(('page_text_box', {**box, 'page_id': page.page_id,
                        'versions': dict(page.versions), 'provenance': ctx.provenance()})
                       for box in reading['boxes'])
        insert_records(ctx.session, records, claim_id=env.claim_id, input_revision=env.input_revision,
                       stage='page_read', job_key=env.job_key, now=ctx.now)
        payload = page_read_event_payload(page)
        job = ctx.job or {}
        message = build_message(PAGE_EVENT_TOPIC, claim_id=env.claim_id, input_revision=env.input_revision,
            task=job['task'], versions=env.versions, target=job['target'], attempt_epoch=job['attempt_epoch'],
            trace_id=env.trace_id, occurred_at=ctx.now, payload=payload, causation_id=env.dedup_key,
            provenance=ctx.provenance())
        ctx.emit(PAGE_EVENT_TOPIC, message)
        return {**payload, 'processing_status': result.processing_status, 'metrics': result.metrics}

    return handler
