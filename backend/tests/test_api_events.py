"""Server-sent data-version notifications: coalesced, immediate on connect, reconnectable."""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy.exc import OperationalError

from panel.api.app import create_app
from panel.api.events import VersionFeed
from panel.store import Writer
from tests.api_support import T0, populate, settings_for
from tests.support import ManualClock, call_row


class FakeTime:
    """Sleeping advances a fake monotonic clock instantly while still yielding to other tasks."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)
        self.now += seconds


def scripted(*versions: int | Exception) -> Any:
    remaining = list(versions)

    async def read() -> int:
        value = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(value, Exception):
            raise value
        return value

    return read


async def published(feed: VersionFeed, count: int, fake: FakeTime) -> list[tuple[int, float]]:
    seen: int | None = None
    events: list[tuple[int, float]] = []
    async with asyncio.timeout(5):
        for _ in range(count):
            seen = await feed.wait_for_change(seen)
            events.append((seen, fake.now))
    return events


async def test_changes_are_published_at_most_once_per_interval() -> None:
    fake = FakeTime()
    feed = VersionFeed(
        scripted(1, 1, 2, 3, 3, 3, 3, 4),
        poll=0.25,
        interval=1.0,
        sleep=fake.sleep,
        clock=fake.clock,
    )
    runner = asyncio.create_task(feed.run())
    events = await published(feed, 3, fake)
    runner.cancel()
    assert events == [(1, 0.0), (3, 1.0), (4, 2.0)]


@pytest.mark.parametrize(
    "failure",
    [OperationalError("SELECT", {}, Exception("database is locked")), RuntimeError("bug")],
    ids=["database", "unexpected"],
)
async def test_read_failures_do_not_stop_the_feed(failure: Exception) -> None:
    fake = FakeTime()
    feed = VersionFeed(
        scripted(failure, 5, failure, 6),
        poll=0.25,
        interval=1.0,
        sleep=fake.sleep,
        clock=fake.clock,
    )
    runner = asyncio.create_task(feed.run())
    events = await published(feed, 2, fake)
    runner.cancel()
    assert [version for version, _ in events] == [5, 6]


@contextmanager
def serving(settings_database: Path, clock: ManualClock) -> Generator[str]:
    app = create_app(
        settings_for(settings_database, event_poll_seconds=0.05, event_min_interval_seconds=1.0),
        clock=clock,
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        loop="uvloop",
        http="httptools",
        ws="none",
        log_level="warning",
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


async def read_events(response: httpx.Response) -> AsyncIterator[dict[str, str]]:
    event: dict[str, str] = {}
    async for line in response.aiter_lines():
        if not line:
            if event:
                yield event
            event = {}
        elif not line.startswith(":"):
            key, _, value = line.partition(":")
            event[key] = value.removeprefix(" ")


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


async def test_stream_sends_the_current_version_then_committed_changes(
    database: Path, clock: ManualClock
) -> None:
    generation = populate(database, clock)
    with serving(database, clock) as base:
        async with (
            httpx.AsyncClient(base_url=base, timeout=10) as client,
            client.stream("GET", "/events") as response,
        ):
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            events = read_events(response)
            first = await anext(events)
            assert (first["event"], first["id"], json.loads(first["data"])) == (
                "version",
                "2",
                {"version": 2},
            )
            assert first["retry"] == "3000"

            committed = time.monotonic()
            writer = Writer(database, clock=clock)
            writer.commit_page(generation, [call_row(7, Call_Received=T0)])
            writer.close()
            second = await anext(events)
            assert second["id"] == "3"
            assert time.monotonic() - committed < 5


async def test_reconnecting_client_resumes_from_its_last_event_id(
    database: Path, clock: ManualClock
) -> None:
    generation = populate(database, clock)
    with serving(database, clock) as base:
        async with httpx.AsyncClient(base_url=base, timeout=10) as client:
            async with client.stream("GET", "/events") as response:
                last_id = (await anext(read_events(response)))["id"]

            writer = Writer(database, clock=clock)
            writer.commit_page(generation, [call_row(7, Call_Received=T0)])
            writer.close()

            reconnected = time.monotonic()
            headers = {"Last-Event-ID": last_id}
            async with client.stream("GET", "/events", headers=headers) as response:
                event = await anext(read_events(response))
            assert (last_id, event["id"]) == ("2", "3")
            assert time.monotonic() - reconnected < 2


@pytest.mark.parametrize("last_event_id", ["1", "not-a-version"])
async def test_stale_or_malformed_last_event_ids_get_the_current_version_at_once(
    database: Path, clock: ManualClock, last_event_id: str
) -> None:
    populate(database, clock)
    with serving(database, clock) as base:
        async with (
            httpx.AsyncClient(base_url=base, timeout=10) as client,
            client.stream("GET", "/events", headers={"Last-Event-ID": last_event_id}) as response,
        ):
            event = await anext(read_events(response))
    assert event["id"] == "2"
