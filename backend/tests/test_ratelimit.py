"""Deterministic tests for the in-memory fixed-window limiter (ADR-012).

The limiter takes an injectable clock, so window expiry and retry delays are
tested without sleeping.
"""

from __future__ import annotations

import ipaddress

from ai_sql_analyst.ratelimit import (
    UNKNOWN_KEY,
    FixedWindowRateLimiter,
    client_key_from_forwarded_for,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Window behavior
# ---------------------------------------------------------------------------


def test_first_ten_requests_allowed_and_eleventh_rejected():
    limiter = FixedWindowRateLimiter(clock=FakeClock())
    decisions = [limiter.check("1.2.3.4") for _ in range(10)]
    rejected = limiter.check("1.2.3.4")

    assert all(decision.allowed for decision in decisions)
    assert rejected.allowed is False
    # A fresh window has the full 60 seconds remaining.
    assert rejected.retry_after == 60
    # Rejected requests do not consume budget or open a new window.
    assert limiter.check("1.2.3.4").retry_after == 60


def test_retry_delay_counts_down_to_the_window_reset():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock=clock)
    for _ in range(10):
        limiter.check("1.2.3.4")

    clock.advance(25)
    assert limiter.check("1.2.3.4").retry_after == 35

    clock.advance(34)  # now 59s into the window
    assert limiter.check("1.2.3.4").retry_after == 1

    clock.advance(1)  # window has elapsed
    assert limiter.check("1.2.3.4").allowed is True


def test_expired_window_resets_without_sleeping():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock=clock)
    for _ in range(10):
        assert limiter.check("1.2.3.4").allowed

    clock.advance(60)
    # The whole budget is available again in the new window.
    assert [limiter.check("1.2.3.4").allowed for _ in range(10)] == [True] * 10
    assert limiter.check("1.2.3.4").allowed is False


def test_expired_keys_are_cleaned_up():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock=clock)
    for key in ("a", "b", "c"):
        limiter.check(key)
    assert len(limiter) == 3

    clock.advance(61)
    # A check on a new key triggers a sweep of the now-expired entries.
    limiter.check("d")

    assert len(limiter) == 1


def test_distinct_keys_have_independent_counters():
    limiter = FixedWindowRateLimiter(clock=FakeClock())
    for _ in range(10):
        limiter.check("1.1.1.1")

    assert limiter.check("1.1.1.1").allowed is False
    # The other key is untouched by the first key's exhaustion.
    assert limiter.check("2.2.2.2").allowed is True


def test_malformed_keys_share_the_unknown_bucket():
    limiter = FixedWindowRateLimiter(clock=FakeClock())
    for _ in range(10):
        assert limiter.check(UNKNOWN_KEY).allowed

    # Any other request that maps to the unknown key shares the same budget.
    assert limiter.check(UNKNOWN_KEY).allowed is False


# ---------------------------------------------------------------------------
# Client-key extraction
# ---------------------------------------------------------------------------


def test_client_key_uses_rightmost_entry():
    assert client_key_from_forwarded_for(["198.51.100.1, 203.0.113.9"]) == "203.0.113.9"


def test_client_key_validates_and_canonicalizes_ipv4_and_ipv6():
    assert client_key_from_forwarded_for(["192.0.2.10"]) == str(
        ipaddress.ip_address("192.0.2.10")
    )
    # IPv6 is validated and returned in canonical form.
    assert client_key_from_forwarded_for(["2001:0db8::0001"]) == str(
        ipaddress.ip_address("2001:0db8::0001")
    )


def test_client_key_preserves_duplicate_header_order():
    # The second header line is later in receipt order, so its value is rightmost.
    assert (
        client_key_from_forwarded_for(["203.0.113.7", "198.51.100.1"])
        == "198.51.100.1"
    )
    # Order is preserved within and across duplicate lines.
    assert (
        client_key_from_forwarded_for(["198.51.100.1, 203.0.113.7", "10.0.0.1"])
        == "10.0.0.1"
    )


def test_client_key_missing_or_malformed_is_unknown():
    assert client_key_from_forwarded_for([]) == UNKNOWN_KEY
    assert client_key_from_forwarded_for([""]) == UNKNOWN_KEY
    assert client_key_from_forwarded_for(["not-an-ip"]) == UNKNOWN_KEY
    # A malformed rightmost entry is not rescued by valid earlier entries.
    assert client_key_from_forwarded_for(["1.2.3.4, garbage"]) == UNKNOWN_KEY
    assert client_key_from_forwarded_for(["1.2.3.4, 999.999.999.999"]) == UNKNOWN_KEY
    assert client_key_from_forwarded_for(["1.2.3.4,"]) == UNKNOWN_KEY
