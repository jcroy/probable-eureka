"""Per-domain rate limiter.

Crawlee provides global rate limiting (max_tasks_per_minute) but not per-domain
throttling. This module adds a thin asyncio-based per-domain delay to ensure
we're polite to individual hosts.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class DomainRateLimiter:
    """Per-domain rate limiter using asyncio locks and timing."""

    def __init__(self, default_rps: float = 1.0, jitter: float = 0.2) -> None:
        """Initialize the rate limiter.

        Args:
            default_rps: Default requests per second per domain.
            jitter: Random jitter fraction (±jitter) to avoid detection patterns.
        """
        self._default_rps = default_rps
        self._jitter = jitter
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_request: dict[str, float] = {}
        self._domain_rps: dict[str, float] = {}

    def set_domain_rps(self, domain: str, rps: float) -> None:
        """Override rate limit for a specific domain."""
        self._domain_rps[domain] = rps

    def _get_delay(self, domain: str) -> float:
        """Get the minimum delay between requests for a domain."""
        rps = self._domain_rps.get(domain, self._default_rps)
        if rps <= 0:
            return 0.0
        return 1.0 / rps

    async def acquire(self, domain: str) -> None:
        """Wait until it's safe to make a request to the given domain.

        This ensures at least (1/rps) seconds between requests to the same domain,
        with optional jitter.
        """
        async with self._locks[domain]:
            delay = self._get_delay(domain)
            if delay <= 0:
                return

            last = self._last_request.get(domain, 0.0)
            elapsed = time.monotonic() - last
            wait = delay - elapsed

            if wait > 0:
                # Add jitter
                import random

                jitter_amount = delay * self._jitter
                wait += random.uniform(-jitter_amount, jitter_amount)
                wait = max(0.0, wait)
                await asyncio.sleep(wait)

            self._last_request[domain] = time.monotonic()
