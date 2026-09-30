"""Bounded, expiring login-failure store for the demonstration sign-in.

Failures are counted per ``(client IP, normalised account)``. The client IP is the real
browser address: behind nginx, uvicorn's proxy-header support (``--forwarded-allow-ips``
limited to the private container networks, see ``infra/Dockerfile.api``) takes it from the
``X-Forwarded-For`` value nginx overwrites with ``$remote_addr``.

Tradeoffs:

* Brute force stays limited per account: one address gets ``limit`` wrong guesses for one
  account per ``window`` seconds, whatever the case or spacing of the email.
* An attacker at another address cannot lock the legitimate user out: their failures
  count against their own address. Clients that share one address (a NAT, or every
  browser reaching a Docker Desktop published port through the same gateway) share a
  bucket, so one of them can delay the others by at most ``window`` seconds after the
  last failure; there is no account-wide lockout.
* A distributed attacker gets ``limit`` guesses per address per window. Stopping that
  needs an account-wide policy (for example step-up verification) that this demo does not
  implement.
* Memory is bounded: at most ``max_keys`` buckets, least recently used evicted first, and
  every bucket expires ``window`` seconds after its last failure. Evicting a bucket forgets
  its failures; an attacker must create ``max_keys`` fresh buckets within one window to
  force that for a bucket that is still in use.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import threading
import time


class LoginFailures:
    def __init__(self, *, limit: int = 10, window: float = 60.0, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.max_keys, self.clock = limit, window, max_keys, clock
        self._buckets: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(client_ip: str, account: str) -> tuple[str, str]:
        return client_ip, account.strip().casefold()

    def __len__(self) -> int:
        return len(self._buckets)

    def _expire(self, now: float) -> None:
        while self._buckets:
            key, times = next(iter(self._buckets.items()))
            if now - times[-1] < self.window:
                break  # oldest-touched bucket is still live, so every later one is too
            del self._buckets[key]

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        return [t for t in self._buckets.get(key, ()) if now - t < self.window]

    def blocked(self, client_ip: str, account: str) -> bool:
        with self._lock:
            now = self.clock()
            self._expire(now)
            return len(self._recent(self.key(client_ip, account), now)) >= self.limit

    def record_failure(self, client_ip: str, account: str) -> None:
        with self._lock:
            now = self.clock()
            self._expire(now)
            key = self.key(client_ip, account)
            self._buckets[key] = (self._recent(key, now) + [now])[-self.limit:]
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)

    def clear(self, client_ip: str, account: str) -> None:
        with self._lock:
            self._buckets.pop(self.key(client_ip, account), None)
