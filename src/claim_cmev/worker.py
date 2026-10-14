"""Worker entry point: ``python -m claim_cmev.worker [run|healthcheck] [--role ROLE] [--local]``.

Roles (same image, same handlers, same topics):

- ``combined``: the lean profile's ``cmev-worker-combined``: outbox relay, orchestrator,
  the six fixture stage producers and the consolidator in one process;
- ``orchestrator``, ``producers``, ``consolidator``: the full profile's separate containers;
- ``parts``: the opt-in M1 model worker (``cmev-worker-parts``, its own image). It runs only
  when ``CMEV_PARTS_PRODUCER=real``, which also takes the parts stage away from the fixture
  producers and makes the orchestrator pin the configured checkpoint;
- ``damage`` and ``summary``: the opt-in M2 model worker and M3 worker. They run only when
  ``CMEV_DAMAGE_PRODUCER=real`` (which needs real parts);
- ``image``: ``parts``, ``damage`` and ``summary`` in one process (``cmev-worker-image``).
- ``ocr``: pinned pretrained M4 page reading; enabled by ``CMEV_OCR_PRODUCER=real``.
  It has a separate image for its Paddle/NumPy dependencies and never downloads weights.

Every role runs the outbox relay; a PostgreSQL advisory lock lets only one publish at a
time. Start-up refuses (exit 1) when the database is not at the expected Alembic head, for
roles that consolidate when the rule configuration or the active cost table does not load,
and for a model worker when a checkpoint cannot be loaded and verified. ``--local`` swaps Kafka
for the in-memory transport: a development aid, never evidence that Kafka integration passed.
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
from .orchestration.plan import VersionBundle, real_stage_versions
from .orchestration.services import IMAGE_ROLES, STAGE_ROLES, ROLES, RuntimeSettings, build_runtimes, local_pipeline
from .persistence.migrations import check_head
from .runtime import Database, setting, utcnow

log = logging.getLogger("cmev.worker")
HEARTBEAT = Path(os.getenv("CMEV_HEARTBEAT_PATH", "/tmp/cmev-worker-heartbeat"))
READY_MARKER = HEARTBEAT.with_name(HEARTBEAT.name + ".ready")
HEARTBEAT_MAX_AGE = 30


def startup_checks(database: Database, settings: RuntimeSettings, role: str,
                   consolidator: Consolidator | None) -> dict[str, Any]:
    """Refuse to start on a schema-head mismatch or an unloadable rule config / cost table.

    A real image worker uses neither. It refuses instead while a fixture producer still owns
    one of its stages, because the two would share one consumer group.
    """
    info: dict[str, Any] = {"role": role, "profile": settings.profile, "schema_head": check_head(database.engine)}
    if role in ("lineitems", "penmarks", "document"):
        if settings.lineitems_producer != "real" or settings.ocr_producer != "real":
            raise RuntimeError("real document workers require real M4 and M5 ownership on every service")
        if role in ("penmarks", "document") and settings.penmarks_producer != "real":
            raise RuntimeError("real M6 ownership is required on every service")
        return info
    if role == "ocr":
        if settings.ocr_producer != "real":
            raise RuntimeError("CMEV_OCR_PRODUCER=real is required on every service before starting the OCR worker; "
                               "add infra/compose/docker-compose.ocr.yml to switch ownership together")
        return info
    if role in IMAGE_ROLES:
        stages = IMAGE_ROLES[role]
        if "parts" in stages and settings.parts_producer != "real":
            raise RuntimeError("CMEV_PARTS_PRODUCER is not 'real': the fixture producer consumes "
                               "cmev.cmd.parts-segment.v1. Set CMEV_PARTS_PRODUCER=real on every service first "
                               "(in Compose, add infra/compose/docker-compose.parts.yml or docker-compose.image.yml)")
        if {"damage", "summary"} & set(stages) and settings.damage_producer != "real":
            raise RuntimeError("CMEV_DAMAGE_PRODUCER is not 'real': the fixture producers consume the damage and "
                               "summary commands. Set CMEV_DAMAGE_PRODUCER=real on every service first "
                               "(in Compose, add infra/compose/docker-compose.image.yml)")
        return info
    if not settings.fixture_mode and not all(p == "real" for p in (settings.parts_producer, settings.damage_producer,
            settings.ocr_producer, settings.lineitems_producer, settings.penmarks_producer)):
        raise RuntimeError("CMEV_FIXTURE_MODE=false: remaining stages still need fixture producers")
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


def load_parts_worker() -> tuple[Any, dict[str, Any]]:
    """Load and verify the M1 checkpoint once; returns the command handler and what was loaded.

    Imported here so that no other role loads the model stack.
    """
    from .storage import Storage
    from .vision.parts.adapter import load_parts_segmenter, make_parts_handler
    from .vision.parts.config import load_parts_config

    segmenter = load_parts_segmenter(load_parts_config())
    versions = real_stage_versions("parts")
    return (make_parts_handler(segmenter, Storage(), versions),
            {"parts_versions": versions, "model_dir": str(segmenter.model_dir), "device": str(segmenter.device)})


def load_damage_worker() -> tuple[Any, dict[str, Any]]:
    """Load and verify the M2 checkpoint once; returns the command handler and what was loaded."""
    from .storage import Storage
    from .vision.damage.adapter import load_damage_segmenter, make_damage_handler
    from .vision.damage.config import load_assignment_config
    from .vision.damage.model_config import load_damage_model_config

    segmenter = load_damage_segmenter(load_damage_model_config())
    versions = real_stage_versions("damage")
    return (make_damage_handler(segmenter, Storage(), versions, load_assignment_config()),
            {"damage_versions": versions, "damage_model_dir": str(segmenter.model_dir),
             "damage_device": str(segmenter.device)})


def load_summary_worker() -> tuple[Any, dict[str, Any]]:
    """The M3 handler over the versioned summary rules. It loads no weights."""
    from .storage import Storage
    from .vision.multiview.adapter import make_summary_handler
    from .vision.multiview.config import load_summary_config

    versions = real_stage_versions("summary")
    return make_summary_handler(Storage(), versions, load_summary_config()), {"summary_versions": versions}


def load_page_read_worker() -> tuple[Any, dict[str, Any]]:
    """Verify the pinned registry and load pretrained OCR once, with no downloads at startup."""
    from .storage import Storage
    from .documents.text_layout.config import load_page_reading_config
    from .documents.text_layout.model_config import load_ocr_model_config, verify_ocr_registry
    from .documents.text_layout.paddle import PaddleOcrEngine
    from .documents.text_layout.worker import make_page_read_handler

    model, page_config = load_ocr_model_config(), load_page_reading_config()
    verify_ocr_registry(model)
    engine = PaddleOcrEngine.from_config(model.model_dir(), page_config)
    storage = Storage()
    storage.ready()
    versions = real_stage_versions("page_read")
    return make_page_read_handler(engine, storage, versions, page_config), {
        "ocr_versions": versions, "ocr_model_dir": str(model.model_dir()), "ocr_granularity": engine.info.granularity}


def load_line_items_worker():
    from .storage import Storage
    from .documents.model_config import load_document_model_config
    from .documents.line_items.config import load_layout_families
    from .documents.line_items.vocabulary import load_estimate_vocabulary
    from .documents.line_items.layoutlmv3 import LocalLayoutLMv3
    from .documents.line_items.worker import make_line_items_handler

    selection = load_document_model_config("lineitems")
    selection.verify()
    model = LocalLayoutLMv3(selection.model_dir(), selection.device)
    if model.text_unit != selection.text_unit:
        raise ValueError("selected M5 text unit differs from checkpoint")
    storage = Storage()
    storage.ready()
    versions = real_stage_versions("line_items")
    return make_line_items_handler(model, storage, versions, selection, load_layout_families(),
                                   load_estimate_vocabulary()), {"lineitems_versions": versions}


def load_pen_marks_worker():
    from .storage import Storage
    from .documents.model_config import load_document_model_config
    from .documents.pen_marks.config import load_linking_config
    from .documents.pen_marks.detector import load_penmark_detector
    from .documents.pen_marks.worker import make_pen_marks_handler

    selection = load_document_model_config("penmarks")
    selection.verify()
    detector = load_penmark_detector(selection.model_dir(), device=selection.device,
                                     allow_candidate=selection.allow_candidate)
    storage = Storage()
    storage.ready()
    versions = real_stage_versions("pen_marks")
    return make_pen_marks_handler(detector, storage, versions, selection, load_linking_config()), {
        "penmarks_versions": versions}


_STAGE_LOADERS = {"parts": load_parts_worker, "damage": load_damage_worker, "summary": load_summary_worker,
                  "page_read": load_page_read_worker, "line_items": load_line_items_worker, "pen_marks": load_pen_marks_worker}


def run(role: str = "combined", local: bool = False) -> None:
    settings = RuntimeSettings.from_env()
    database = Database()
    consolidator = versions = None
    handlers: dict[str, Any] = {}
    try:
        if role not in STAGE_ROLES:
            consolidator = Consolidator(cost_table_root=settings.cost_table_root)
        info = startup_checks(database, settings, role, consolidator)
        if role in STAGE_ROLES:
            for stage in STAGE_ROLES[role]:
                handlers[f"{stage}_handler"], loaded = _STAGE_LOADERS[stage]()
                info.update(loaded)
        else:
            versions = VersionBundle.for_runtime(settings.parts_producer, settings.damage_producer,
                                                  getattr(settings, "ocr_producer", "fixture"),
                                                  settings.lineitems_producer, settings.penmarks_producer)
    except Exception as exc:  # noqa: BLE001 - refuse to start, never run degraded
        log.error("refusing to start %s: %s", role, exc)
        READY_MARKER.unlink(missing_ok=True)
        raise SystemExit(1) from exc
    log.info("worker ready: %s", info)
    if local:
        log.warning("Explicit local development transport selected; Kafka is not exercised")
        pipeline = local_pipeline(database, settings=settings, role=role, versions=versions, consolidator=consolidator,
                                  parts_producer=settings.parts_producer, damage_producer=settings.damage_producer,
                                  ocr_producer=settings.ocr_producer, lineitems_producer=settings.lineitems_producer,
                                  penmarks_producer=settings.penmarks_producer,
                                  **handlers)
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
        producer = None
        try:
            created = ensure_topics(servers, TOPICS)
            if created:
                log.info("created topics %s", created)
            producer = KafkaProducerTransport(servers, client)
            runtimes = build_runtimes(database.session, role, versions=versions, consolidator=consolidator,
                                      profile=settings.profile, source_kind=settings.source_kind,
                                      parts_producer=settings.parts_producer,
                                      damage_producer=settings.damage_producer,
                                      ocr_producer=getattr(settings, "ocr_producer", "fixture"),
                                      lineitems_producer=settings.lineitems_producer,
                                      penmarks_producer=settings.penmarks_producer, **handlers)
            for rt in runtimes:
                consumers.append((rt, KafkaConsumerTransport(servers, rt.group, rt.topics, client)))
            READY_MARKER.write_text(str(info))
            while True:
                relay_once(database.session, producer, clock=utcnow)
                for runtime, consumer in consumers:
                    consume_batch(runtime, consumer)
                _beat()
        except (TransportUnavailable, OSError, RuntimeError, Exception):  # noqa: BLE001
            log.exception("worker transport or database unavailable; uncommitted offsets will be redelivered")
        finally:
            for _runtime, consumer in consumers:
                try:
                    consumer.close()
                except Exception:
                    pass
            if producer is not None:
                try:
                    producer.close()
                except Exception:
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
