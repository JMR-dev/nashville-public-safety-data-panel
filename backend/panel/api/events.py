"""Server-sent notifications that committed call data changed.

One background task per API process polls the local database, so browser connections never
cause upstream requests or extra database polling. Changes are published at most once per
interval; each event carries the data version as its id so reconnecting clients resume.
"""

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.sse import EventSourceResponse, ServerSentEvent

RETRY_MS = 3000
log = logging.getLogger(__name__)


class VersionFeed:
    def __init__(
        self,
        read_version: Callable[[], Awaitable[int]],
        *,
        poll: float,
        interval: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read_version
        self._poll = poll
        self._interval = interval
        self._sleep = sleep
        self._clock = clock
        self._changed = asyncio.Condition()
        self.version: int | None = None

    async def run(self) -> None:
        published = -math.inf
        while True:
            try:
                current = await self._read()
                if current != self.version:
                    wait = published + self._interval - self._clock()
                    if wait > 0:
                        await self._sleep(wait)
                        current = await self._read()
                    published = self._clock()
                    async with self._changed:
                        self.version = current
                        self._changed.notify_all()
            except Exception:
                # A notification outage must never end the loop: clients would wait forever.
                log.exception("Could not read the data version")
            await self._sleep(self._poll)

    async def wait_for_change(self, seen: int | None) -> int:
        async with self._changed:
            await self._changed.wait_for(lambda: self.version not in (None, seen))
            assert self.version is not None
            return self.version


def events_router(feed: VersionFeed) -> APIRouter:
    router = APIRouter()

    @router.get("/events", response_class=EventSourceResponse)
    async def events(
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> AsyncIterable[ServerSentEvent]:
        # A reconnecting client that already has the current version waits for the next one.
        seen = int(last_event_id) if last_event_id and last_event_id.isdecimal() else None
        while True:
            seen = await feed.wait_for_change(seen)
            yield ServerSentEvent(
                event="version", id=str(seen), data={"version": seen}, retry=RETRY_MS
            )

    return router
