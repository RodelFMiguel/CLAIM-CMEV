"""Service wiring: which consumer groups a role runs, and the in-process pipeline.

The same handlers and the same topics run in every packaging: ``combined`` (the lean
profile's one worker), or ``orchestrator``, ``producers`` and ``consolidator`` as separate
containers of the full profile. ``LocalPipeline`` drives them over the in-memory broker
for tests, ``--local`` development and the one-time seed at API start.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import time
from typing import Any

from ..costs.reference import CostTableError, active_table_version
from ..messaging.consumer import ConsumerRuntime, RetryPolicy, consume_batch
from ..messaging.outbox import relay_once
from ..messaging.transport import InMemoryBroker, InMemoryProducer
from ..runtime import utcnow
from . import state
from .consolidation import Consolidator
from .orchestrator import GROUP as ORCHESTRATOR_GROUP, Orchestrator
from .plan import VersionBundle

ROLES = ("combined", "orchestrator", "producers", "consolidator")
REPO_COST_TABLES = Path(__file__).resolve().parents[3] / "artifacts" / "cost_tables"


@dataclass(frozen=True)
class RuntimeSettings:
    profile: str
    fixture_mode: bool
    cost_table_root: Path
    configured_table_version: str | None

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        profile = os.getenv("CMEV_PROFILE", "lean")
        if profile not in ("lean", "full"):
            raise RuntimeError(f"CMEV_PROFILE must be lean or full, not {profile!r}")
        root = os.getenv("CMEV_COST_TABLE_PATH") or str(REPO_COST_TABLES)
        return cls(profile=profile, fixture_mode=os.getenv("CMEV_FIXTURE_MODE", "true").lower() == "true",
                   cost_table_root=Path(root), configured_table_version=os.getenv("CMEV_ACTIVE_COST_TABLE_VERSION") or None)

    @property
    def source_kind(self) -> str:
        return "fixture" if self.fixture_mode else "real"

    def active_cost_table(self) -> str:
        """The version new assessments pin; raises ``CostTableError`` when none resolves."""
        version = self.configured_table_version or active_table_version(self.cost_table_root)
        if not version:
            raise CostTableError("table_missing", f"no active cost table under {self.cost_table_root}")
        return version


def build_runtimes(session_factory: Any, role: str, *, versions: VersionBundle, consolidator: Consolidator,
                   profile: str, source_kind: str = "fixture", clock: Callable[[], datetime] = utcnow,
                   sleep: Callable[[float], None] = time.sleep, retry: RetryPolicy | None = None) -> list[ConsumerRuntime]:
    """One ``ConsumerRuntime`` per consumer group the role runs."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; choose from {ROLES}")
    from ..fixtures import FixtureProducers

    common = {"profile": profile, "source_kind": source_kind, "clock": clock, "sleep": sleep, "retry": retry,
              "is_superseded": state.is_superseded}
    runtimes = []
    if role in ("combined", "orchestrator"):
        orchestrator = Orchestrator(versions, consolidator.config.rules_config_version)
        runtimes.append(ConsumerRuntime(session_factory, ORCHESTRATOR_GROUP, orchestrator.handlers(),
                                        service=ORCHESTRATOR_GROUP, on_dead_letter=orchestrator.on_dead_letter,
                                        **common))
    if role in ("combined", "producers"):
        if source_kind != "fixture":
            raise RuntimeError("only fixture producers exist; real module workers are not implemented")
        for group, handlers in FixtureProducers(versions).groups().items():
            runtimes.append(ConsumerRuntime(session_factory, group, handlers, service=group, **common))
    if role in ("combined", "consolidator"):
        runtimes.append(ConsumerRuntime(session_factory, "cmev-consolidator", consolidator.handlers(),
                                        service="cmev-consolidator", **common))
    return runtimes


class LocalPipeline:
    """Relay plus every runtime over one in-memory broker. A development transport only."""

    def __init__(self, session_factory: Any, runtimes: Iterable[ConsumerRuntime], *,
                 broker: InMemoryBroker | None = None, clock: Callable[[], datetime] = utcnow):
        self.session_factory, self.clock = session_factory, clock
        self.broker = broker or InMemoryBroker()
        self.producer = InMemoryProducer(self.broker)
        self.runtimes = list(runtimes)
        self.consumers = [(rt, self.broker.consumer(rt.group, rt.topics)) for rt in self.runtimes]

    def relay(self) -> int:
        return relay_once(self.session_factory, self.producer, clock=self.clock)

    def tick(self) -> int:
        moved = self.relay()
        for runtime, consumer in self.consumers:
            moved += consume_batch(runtime, consumer)
        return moved

    def drain(self, max_rounds: int = 500) -> int:
        total = 0
        for _ in range(max_rounds):
            moved = self.tick()
            total += moved
            if not moved:
                return total
        raise RuntimeError("local pipeline did not settle")

    def runtime(self, group: str) -> ConsumerRuntime:
        return next(rt for rt in self.runtimes if rt.group == group)


def local_pipeline(database: Any, *, settings: RuntimeSettings | None = None, role: str = "combined",
                   versions: VersionBundle | None = None, consolidator: Consolidator | None = None,
                   clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], None] = time.sleep,
                   retry: RetryPolicy | None = None, broker: InMemoryBroker | None = None) -> LocalPipeline:
    settings = settings or RuntimeSettings.from_env()
    consolidator = consolidator or Consolidator(cost_table_root=settings.cost_table_root)
    runtimes = build_runtimes(database.session, role, versions=versions or VersionBundle.fixture(),
                              consolidator=consolidator, profile=settings.profile, source_kind=settings.source_kind,
                              clock=clock, sleep=sleep, retry=retry)
    return LocalPipeline(database.session, runtimes, broker=broker, clock=clock)


__all__ = ["LocalPipeline", "ROLES", "RuntimeSettings", "build_runtimes", "local_pipeline"]
