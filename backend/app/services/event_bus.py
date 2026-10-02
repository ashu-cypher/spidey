"""MISSION MEW — process-global asyncio event bus.

A minimal pub/sub: ``publish(event)`` fans an event dict out to every queue
created by ``subscribe()``; subscribers consume their own ``asyncio.Queue``
(or use ``events()`` for an async-generator view). Queues are bounded so a
slow SSE client can never grow memory without limit — on overflow the event
is dropped for that subscriber only.

Synchronous ``publish``/``subscribe``/``unsubscribe``: safe to call from
async code (the SSE route, the reminder poller) without awaiting.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("spidey")

_MAX_QUEUE = 200

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    """Create and register a new subscriber queue."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.discard(queue)


def publish(event: dict) -> int:
    """Fan *event* out to all subscribers. Returns the delivery count."""
    delivered = 0
    for queue in list(_subscribers):
        try:
            queue.put_nowait(dict(event))
            delivered += 1
        except asyncio.QueueFull:
            # Slow consumer: drop for this subscriber only, keep the bus alive.
            logger.debug("event bus: dropped event for a slow subscriber")
    return delivered


async def events():
    """Async-generator view over a fresh subscription (auto-unsubscribes)."""
    queue = subscribe()
    try:
        while True:
            yield await queue.get()
    finally:
        unsubscribe(queue)


def subscriber_count() -> int:
    return len(_subscribers)
