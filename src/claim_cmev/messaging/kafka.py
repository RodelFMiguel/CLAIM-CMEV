"""Kafka transport over the installed ``kafka-python`` client.

The technical specification proposes ``confluent-kafka``; this pass keeps ``kafka-python``
(already a dependency) because it gives the same explicit offset control:
``enable_auto_commit=False`` and ``commit()`` after the database transaction. This is the
only module that imports a Kafka client.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
import json
import logging
from typing import Any

from .transport import DLQ_TOPIC, Message, TransportUnavailable, partitions_for

log = logging.getLogger("cmev.kafka")
SEND_TIMEOUT = 10
MAX_REQUEST_BYTES = 1024 * 1024


def _decode(raw: bytes | None) -> Any:
    """JSON when parseable; otherwise the raw text, so the runtime can dead-letter it."""
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class KafkaProducerTransport:
    def __init__(self, bootstrap: str, client_id: str):
        from kafka import KafkaProducer

        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap, client_id=client_id, acks="all", max_request_size=MAX_REQUEST_BYTES,
            key_serializer=lambda k: k.encode() if k is not None else None,
            value_serializer=lambda v: json.dumps(v, separators=(",", ":")).encode())

    def send(self, topic: str, key: str, value: Any) -> None:
        from kafka.errors import KafkaError

        try:
            self._producer.send(topic, key=key, value=value).get(timeout=SEND_TIMEOUT)
        except KafkaError as exc:
            raise TransportUnavailable(f"publish to {topic} failed: {type(exc).__name__}") from exc

    def close(self) -> None:
        self._producer.close(timeout=5)


class KafkaConsumerTransport:
    """One consumer-group member. Offsets are committed only by ``commit()``."""

    def __init__(self, bootstrap: str, group: str, topics: Sequence[str], client_id: str,
                 max_poll_interval_ms: int = 300_000):
        from kafka import KafkaConsumer

        self.group, self.topics = group, tuple(topics)
        self._consumer = KafkaConsumer(
            *self.topics, bootstrap_servers=bootstrap, group_id=group, client_id=client_id,
            enable_auto_commit=False, auto_offset_reset="earliest", max_poll_interval_ms=max_poll_interval_ms,
            key_deserializer=lambda k: k.decode() if k is not None else None, value_deserializer=_decode)

    def poll(self, max_records: int = 50) -> list[Message]:
        from kafka.errors import KafkaError

        try:
            batches = self._consumer.poll(timeout_ms=100, max_records=max_records)
        except KafkaError as exc:
            raise TransportUnavailable(f"poll for {self.group} failed: {type(exc).__name__}") from exc
        records = [r for part in batches.values() for r in part]
        return [Message(r.topic, r.key, r.value, r.partition, r.offset) for r in records]

    def assigned(self) -> bool:
        return bool(self._consumer.assignment())

    def commit(self) -> None:
        self._consumer.commit()

    def close(self) -> None:
        self._consumer.close(autocommit=False)


def ensure_topics(bootstrap: str, topics: Iterable[str], retention_ms: dict[str, int] | None = None) -> list[str]:
    """Create missing topics explicitly (auto-creation is off). Idempotent; returns created names."""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError

    admin = KafkaAdminClient(bootstrap_servers=bootstrap, client_id="cmev-bootstrap")
    try:
        existing = set(admin.list_topics())
        wanted = [t for t in topics if t not in existing]
        created = []
        for topic in wanted:
            days = 30 if topic in (DLQ_TOPIC, "cmev.evt.assessment-ready.v1", "cmev.evt.job-failed.v1") else 7
            config = {"cleanup.policy": "delete",
                      "retention.ms": str((retention_ms or {}).get(topic, days * 86_400_000))}
            try:
                admin.create_topics([NewTopic(topic, num_partitions=partitions_for(topic), replication_factor=1,
                                              topic_configs=config)])
                created.append(topic)
            except TopicAlreadyExistsError:
                pass
        return created
    finally:
        admin.close()


__all__ = ["KafkaConsumerTransport", "KafkaProducerTransport", "ensure_topics"]
