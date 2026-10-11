"""Best-effort, process-local rate limiting for the HTTP boundary (ADR-012).

A small standard-library fixed-window limiter used to blunt abuse of the
analytical endpoint. It is deliberately *not* a global quota: state lives in one
process, so it resets on restart and each Cloud Run instance counts separately
(see ADR-012). It adds no dependencies, external services, or configuration.

The client key is the rightmost address in the received ``X-Forwarded-For``
chain. That is a documented best-effort assumption about the observed Cloud Run
request path, not a guarantee of spoof-proof identity: it discourages casual
abuse but does not by itself prevent deliberate IP manipulation.
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Policy: 10 requests per 60-second fixed window, per client key.
DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 60.0

# Shared bucket for requests whose client address cannot be established. Using a
# single bucket (rather than the internal peer address or failing open) keeps
# unidentifiable traffic rate-limited without trusting a proxy-internal address.
UNKNOWN_KEY = "unknown"


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of one rate-limit check."""

    allowed: bool
    # Whole seconds until the current window resets; 0 when the request is
    # allowed. Suitable for a ``Retry-After`` header.
    retry_after: int = 0


class FixedWindowRateLimiter:
    """A per-key fixed-window request counter.

    The first request for a key opens a window; the next ``limit`` requests are
    allowed and the rest are rejected until the window elapses. Expired entries
    are removed during checks, at most one sweep per window, so no background
    task is needed and memory stays bounded for sporadic traffic.

    ``clock`` is injectable (defaults to ``time.monotonic``) so window behavior
    can be tested deterministically. Counter updates are guarded by a lock so the
    limiter is safe if FastAPI runs a synchronous dependency in a worker thread.
    """

    def __init__(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (window_start, count)
        self._windows: dict[str, tuple[float, int]] = {}
        self._last_sweep = clock()

    def check(self, key: str) -> RateLimitDecision:
        """Record one request for ``key`` and report whether it is allowed."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            entry = self._windows.get(key)
            if entry is None or now >= entry[0] + self._window:
                self._windows[key] = (now, 1)
                return RateLimitDecision(allowed=True)
            start, count = entry
            if count < self._limit:
                self._windows[key] = (start, count + 1)
                return RateLimitDecision(allowed=True)
            remaining = start + self._window - now
            return RateLimitDecision(
                allowed=False, retry_after=max(1, math.ceil(remaining))
            )

    def _sweep(self, now: float) -> None:
        """Drop expired windows at most once per window, bounding memory growth."""
        if now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        expired = [
            key
            for key, (start, _) in self._windows.items()
            if now >= start + self._window
        ]
        for key in expired:
            del self._windows[key]

    def __len__(self) -> int:
        """Number of tracked keys (test/introspection helper)."""
        with self._lock:
            return len(self._windows)


def client_key_from_forwarded_for(values: Sequence[str] | Iterable[str]) -> str:
    """Return the rightmost valid IP across the forwarded chain, else ``unknown``.

    Header values are concatenated in receipt order (preserving the order of
    duplicate header lines) and split on commas, so the last entry is the value
    added by the hop closest to the application. Only that last entry is trusted;
    earlier entries are attacker-influenced and are not used as the key.
    """
    entries: list[str] = []
    for value in values:
        entries.extend(part.strip() for part in value.split(","))
    if not entries:
        return UNKNOWN_KEY
    rightmost = entries[-1]
    if not rightmost:
        return UNKNOWN_KEY
    try:
        return str(ipaddress.ip_address(rightmost))
    except ValueError:
        return UNKNOWN_KEY
