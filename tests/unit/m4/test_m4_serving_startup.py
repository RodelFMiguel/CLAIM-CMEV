"""Independent startup checks for storage and configured OCR retry limits."""
from types import SimpleNamespace

import pytest

from claim_cmev import worker
from claim_cmev.documents.text_layout import config, model_config, paddle
from claim_cmev.messaging.consumer import RetryPolicy
from claim_cmev.orchestration.services import build_runtimes


def test_ocr_retry_count_uses_config_and_explicit_policy_remains_authoritative(monkeypatch):
    selected = config.load_page_reading_config().with_overrides({'ocr': {'max_attempts': 1}})
    monkeypatch.setattr(config, 'load_page_reading_config', lambda: selected)
    runtime = build_runtimes(None, 'ocr', profile='lean', page_read_handler=lambda _: {})[0]
    assert runtime.retry.max_attempts == 1
    explicit = RetryPolicy(max_attempts=2)
    runtime = build_runtimes(None, 'ocr', profile='lean', page_read_handler=lambda _: {}, retry=explicit)[0]
    assert runtime.retry is explicit


def test_unavailable_object_store_prevents_ocr_readiness(monkeypatch):
    from claim_cmev import storage

    calls = []
    monkeypatch.setattr(model_config, 'verify_ocr_registry', lambda _: calls.append('registry'))
    monkeypatch.setattr(paddle.PaddleOcrEngine, 'from_config', lambda *_: SimpleNamespace())

    class UnavailableStorage:
        def ready(self):
            calls.append('storage')
            raise RuntimeError('storage unavailable')

    monkeypatch.setattr(storage, 'Storage', UnavailableStorage)
    with pytest.raises(RuntimeError, match='storage unavailable'):
        worker.load_page_read_worker()
    assert calls == ['registry', 'storage']
