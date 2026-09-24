"""Worker entry point: ``python -m claim_cmev.worker [run|healthcheck] [--role ROLE] [--local]``.

Roles (same image, same handlers, same topics):

- ``combined``: the lean profile's ``cmev-worker-combined``: outbox relay, orchestrator,
  the six fixture stage producers and the consolidator in one process;
- ``orchestrator``, ``producers``, ``consolidator``: the full profile's separate containers.

Every role runs the outbox relay; a PostgreSQL advisory lock lets only one publish at a
time. Start-up refuses (exit 1) when the database is not at the expected Alembic head or,
for roles that consolidate, when the rule configuration or the active cost table does not
load. ``--local`` swaps Kafka for the in-memory transport: a development aid, never
evidence that Kafka integration passed.
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import socket
import sys
import time
from typing import Any

from .contracts.events import TOPICS
from .messaging.consumer import consume_batch
from .messaging.outbox import relay_once
from .messaging.transport import TransportUnavailable
from .orchestration.consolidation import Consolidator
from .orchestration.plan import VersionBundle
from .orchestration.services import ROLES, RuntimeSettings, build_runtimes, local_pipeline
from .persistence.migrations import check_head
from .runtime import Database, setting, utcnow

log = logging.getLogger("cmev.worker")
HEARTBEAT = Path(os.getenv("CMEV_HEARTBEAT_PATH", "/tmp/cmev-worker-heartbeat"))
READY_MARKER = HEARTBEAT.with_name(HEARTBEAT.name + ".ready")
HEARTBEAT_MAX_AGE = 30


def startup_checks(database: Database, settings: RuntimeSettings, role: str,
                   consolidator: Consolidator | None) -> dict[str, Any]:
    """Refuse to start on a schema-head mismatch or an unloadable rule config / cost table."""
    info: dict[str, Any] = {"role": role, "profile": settings.profile, "schema_head": check_head(database.engine)}
    if not settings.fixture_mode:
        raise RuntimeError("CMEV_FIXTURE_MODE=false: only fixture stage producers exist; real inference is unavailable")
    if consolidator is None:
        raise RuntimeError("M8 rule configuration missing")
    info["rules_config_version"] = consolidator.config.rules_config_version  # the orchestrator signs jobs with it
    if role in ("combined", "consolidator"):
        info.update(consolidator.readiness(settings.configured_table_version))
    return info


def local_tick(database: Database) -> int:
    """Drain every unpublished outbox row through the in-process pipeline (tests, development)."""
    return local_pipeline(database, sleep=lambda _s: None).drain()


def _beat() -> None:
    HEARTBEAT.write_text(str(time.time()))


def run(role: str = "combined", local: bool = False) -> None:
    settings = RuntimeSettings.from_env()
    database = Database()
    consolidator = Consolidator(cost_table_root=settings.cost_table_root)
    try:
        info = startup_checks(database, settings, role, consolidator)
    except Exception as exc:  # noqa: BLE001 - refuse to start, never run degraded
        log.error("refusing to start %s: %s", role, exc)
        READY_MARKER.unlink(missing_ok=True)
        raise SystemExit(1) from exc
    log.info("worker ready: %s", info)
    versions = VersionBundle.fixture()
    if local:
        log.warning("Explicit local development transport selected; Kafka is not exercised")
        pipeline = local_pipeline(database, settings=settings, role=role, versions=versions, consolidator=consolidator)
        READY_MARKER.write_text(str(info))
        while True:
            pipeline.tick()
            _beat()
            time.sleep(1)
    from .messaging.kafka import KafkaConsumerTransport, KafkaProducerTransport, ensure_topics

    servers = setting("KAFKA_BOOTSTRAP", "localhost:9092", "KAFKA_BOOTSTRAP_SERVERS")
    client = f"{role}-{socket.gethostname()}"
    while True:
        consumers = []
        try:
            created = ensure_topics(servers, TOPICS)
            if created:
                log.info("created topics %s", created)
            producer = KafkaProducerTransport(servers, client)
            runtimes = build_runtimes(database.session, role, versions=versions, consolidator=consolidator,
                                      profile=settings.profile, source_kind=settings.source_kind)
            consumers = [(rt, KafkaConsumerTransport(servers, rt.group, rt.topics, client)) for rt in runtimes]
            READY_MARKER.write_text(str(info))
            while True:
                relay_once(database.session, producer, clock=utcnow)
                for runtime, consumer in consumers:
                    consume_batch(runtime, consumer)
                _beat()
        except (TransportUnavailable, OSError, RuntimeError, Exception):  # noqa: BLE001
            log.exception("worker transport or database unavailable; uncommitted offsets will be redelivered")
            for _runtime, consumer in consumers:
                try:
                    consumer.close()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(3)


def healthy() -> bool:
    """Ready marker written after start-up checks, and a fresh heartbeat from the poll loop."""
    return READY_MARKER.exists() and HEARTBEAT.exists() and time.time() - float(HEARTBEAT.read_text()) < HEARTBEAT_MAX_AGE


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="run", choices=["run", "healthcheck"])
    parser.add_argument("--role", default=os.getenv("CMEV_WORKER_ROLE", "combined"), choices=ROLES)
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "healthcheck":
        raise SystemExit(0 if healthy() else 1)
    logging.basicConfig(level=os.getenv("CMEV_LOG_LEVEL", "INFO"), stream=sys.stdout)
    run(args.role, args.local)


if __name__ == "__main__":
    main()
