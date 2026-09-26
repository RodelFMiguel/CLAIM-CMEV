"""Reconnect closes resources even when only part of consumer startup succeeds."""
from types import SimpleNamespace
import pytest
from claim_cmev import worker
from claim_cmev.messaging import kafka


def test_partial_consumer_startup_closes_producer_and_created_consumers(monkeypatch):
    settings = SimpleNamespace(cost_table_root="unused", profile="lean", source_kind="fixture")
    monkeypatch.setattr(worker.RuntimeSettings, "from_env", lambda: settings)
    monkeypatch.setattr(worker, "Database", lambda: SimpleNamespace(session=None))
    monkeypatch.setattr(worker, "Consolidator", lambda **_: object())
    monkeypatch.setattr(worker, "startup_checks", lambda *_: {})
    monkeypatch.setattr(kafka, "ensure_topics", lambda *_: [])
    closed = []
    monkeypatch.setattr(kafka, "KafkaProducerTransport", lambda *_: SimpleNamespace(close=lambda: closed.append("producer")))
    count = 0
    def consumer(*_):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("second consumer unavailable")
        return SimpleNamespace(close=lambda: closed.append("consumer"))
    monkeypatch.setattr(kafka, "KafkaConsumerTransport", consumer)
    monkeypatch.setattr(worker, "build_runtimes", lambda *_, **__: [SimpleNamespace(group="a", topics=[]), SimpleNamespace(group="b", topics=[])])
    def stop(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(worker.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        worker.run()
    assert closed == ["consumer", "producer"]
