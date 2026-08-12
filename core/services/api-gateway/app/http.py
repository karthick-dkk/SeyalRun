"""Shared pooled HTTP client for upstream calls.

A single AsyncClient keeps connections alive across requests instead of paying a
fresh TCP (and, once inter-service mTLS lands, TLS) handshake per call. Per-call
timeouts are passed at each call site. Closed on app shutdown by the lifespan.
"""

from __future__ import annotations

import httpx

# Default timeout is a floor; latency-sensitive call sites pass their own per call
# (e.g. the proxy passes 30s for long upstream requests).
client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))


async def aclose() -> None:
    await client.aclose()
