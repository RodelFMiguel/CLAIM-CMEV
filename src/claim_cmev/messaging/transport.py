"""Transport protocol plus the in-memory broker used by tests and ``--local``.

Kafka semantics the runtime relies on, reproduced here: messages keyed by ``claim_id``
land on one partition (per-key order), each consumer group keeps its own committed
offsets, ``poll`` advances an in-memory position, ``commit`` persists it, and a restart
resumes from the last committed offset (redelivery). Values cross the boundary as JSON
text, as on the wire. The in-memory broker is a development transport, never evidence
that Kafka integration has passed.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
from typing import Any, Protocol
import zlib

DEFAULT_PARTITIONS = 3
DLQ_TOPIC = "cmev.dlq.v1"


class TransportUnavailable(RuntimeError):
    """The broker could not accept or deliver a message; pending work is retained."""


@dataclass(frozen=True)
class Message:
    topic: str
    key: str | None
    value: Any
    partition: int
    offset: int


class Producer(Protocol):
    def send(self, topic: str, key: str, value: Any) -> None: ...


class Consumer(Protocol):
    group: str
    topics: tuple[str, ...]

    def poll(self, max_records: int = 50) -> list[Message]: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


def partitions_for(topic: str) -> int:
    return 1 if topic == DLQ_TOPIC else DEFAULT_PARTITIONS


def partition_for(topic: str, key: str | None) -> int:
    """Stable key partitioning: one claim always lands on one partition."""
    return zlib.crc32((key or "").encode()) % partitions_for(topic)


def _wire(value: Any) -> Any:
    """Round-trip through JSON text like a real broker; non-JSON text stays a string."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.loads(json.dumps(value))


class InMemoryBroker:
    """Partitioned append-only logs with per-group committed offsets."""

    def __init__(self) -> None:
        self.logs: dict[tuple[str, int], list[Message]] = defaultdict(list)
        self.committed: dict[tuple[str, str, int], int] = {}
        self.available = True

    def send(self, topic: str, key: str | None, value: Any) -> Message:
        if not self.available:
            raise TransportUnavailable("in-memory broker marked unavailable")
        partition = partition_for(topic, key)
        log = self.logs[(topic, partition)]
        message = Message(topic, key, _wire(value), partition, len(log))
        log.append(message)
        return message

    def messages(self, topic: str) -> list[Message]:
        found = [m for (t, _p), log in self.logs.items() if t == topic for m in log]
        return sorted(found, key=lambda m: (m.partition, m.offset))

    def consumer(self, group: str, topics: Iterable[str]) -> InMemoryConsumer:
        return InMemoryConsumer(self, group, tuple(topics))

    def lag(self, group: str, topics: Iterable[str]) -> int:
        return sum(len(self.logs[(t, p)]) - self.committed.get((group, t, p), 0)
                   for t in topics for p in range(partitions_for(t)))


class InMemoryProducer:
    def __init__(self, broker: InMemoryBroker):
        self.broker = broker

    def send(self, topic: str, key: str, value: Any) -> None:
        self.broker.send(topic, key, value)


class InMemoryConsumer:
    """One group member owning every partition of its topics (at most one per partition)."""

    def __init__(self, broker: InMemoryBroker, group: str, topics: Sequence[str]):
        self.broker, self.group, self.topics = broker, group, tuple(topics)
        self.positions: dict[tuple[str, int], int] = {}
        self.restart()

    def restart(self) -> None:
        """Simulate a consumer restart: resume from the last committed offsets."""
        self.positions = {(t, p): self.broker.committed.get((self.group, t, p), 0)
                          for t in self.topics for p in range(partitions_for(t))}

    def poll(self, max_records: int = 50) -> list[Message]:
        if not self.broker.available:
            raise TransportUnavailable("in-memory broker marked unavailable")
        batch: list[Message] = []
        for (topic, partition), position in sorted(self.positions.items()):
            log = self.broker.logs[(topic, partition)]
            take = log[position:position + max(0, max_records - len(batch))]
            batch.extend(take)
            self.positions[(topic, partition)] = position + len(take)
            if len(batch) >= max_records:
                break
        return batch

    def commit(self) -> None:
        for (topic, partition), position in self.positions.items():
            self.broker.committed[(self.group, topic, partition)] = position

    def close(self) -> None:
        self.restart()


__all__ = [
    "DEFAULT_PARTITIONS", "DLQ_TOPIC", "Consumer", "InMemoryBroker", "InMemoryConsumer", "InMemoryProducer",
    "Message", "Producer", "TransportUnavailable", "partition_for", "partitions_for",
]
