"""Poisson arrival scheduler.

Inter-arrival times are exponentially distributed with rate ``qps``; arrival
events therefore form a Poisson process. The scheduler awaits each arrival
in absolute time so cumulative drift is bounded.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import AsyncIterator, Sequence, TypeVar

T = TypeVar("T")


def arrival_offsets(num: int, qps: float, seed: int) -> list[float]:
    """Return cumulative arrival offsets (seconds from t=0) for ``num`` events."""
    if qps <= 0:
        raise ValueError("qps must be > 0")
    rng = random.Random(seed)
    offsets: list[float] = []
    t = 0.0
    for _ in range(num):
        t += rng.expovariate(qps)
        offsets.append(t)
    return offsets


async def arrivals(
    items: Sequence[T], qps: float, seed: int
) -> AsyncIterator[tuple[int, T, float]]:
    """Yield ``(index, item, scheduled_offset)`` at each Poisson arrival."""
    offsets = arrival_offsets(len(items), qps, seed)
    start = time.monotonic()
    for i, (item, off) in enumerate(zip(items, offsets)):
        delay = (start + off) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        yield i, item, off
