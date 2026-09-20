"""Helpers for driving the worker against the simulated source."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound, OperationalError

from panel.settings import Settings
from panel.store import Generation, Writer
from panel.tables import calls, source_status
from panel.worker import Worker
from tests.fake_source import FakeSource
from tests.fake_time import FakeTime

SOURCE_URL = "https://source.test/FeatureServer/0"


def build(database: Path, source: FakeSource, clock: FakeTime, **overrides: Any) -> Worker:
    source.now = clock.epoch_ms
    settings = Settings(database=database, source_url=SOURCE_URL, **overrides)
    writer = Writer(database, clock=clock.epoch_ms)
    client = httpx.AsyncClient(
        transport=source.transport(clock.monotonic),
        headers={"User-Agent": settings.user_agent},
    )
    return Worker(
        settings,
        writer,
        client,
        clock=clock.epoch_ms,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


async def run_until(worker: Worker, ready: Callable[[], bool], *, seconds: float = 10) -> None:
    """Run the worker until it reaches a state, then stop it."""
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    try:
        async with asyncio.timeout(seconds):
            while not ready():
                if task.done():
                    await task
                    raise AssertionError("The worker stopped before the expected state")
                # Checking on an interval leaves the worker room to run; checking every loop
                # turn would spend the process on repeated queries instead.
                await asyncio.sleep(0.002)
    finally:
        stop.set()
        await task


def rows(writer: Writer) -> list[dict[str, Any]]:
    """Every stored record, including ones from retired generations."""
    try:
        with writer.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(calls)).mappings()]
    except OperationalError:
        return []


def stored(writer: Writer) -> dict[int, dict[str, Any]]:
    """Records of the newest generation by OBJECTID. Identifiers repeat across generations."""
    found = rows(writer)
    if not found:
        return {}
    newest = max(row["generation"] for row in found)
    return {row["OBJECTID"]: row for row in found if row["generation"] == newest}


def status(writer: Writer, source: str = "mnpd-calls") -> dict[str, Any]:
    """The source's status row, or an empty one before the worker has written it."""
    try:
        with writer.engine.connect() as connection:
            found = connection.execute(
                select(source_status).where(source_status.c.source == source)
            ).mappings()
            return dict(found.one() if found else {})
    except OperationalError, NoResultFound:
        return {}


def generation(writer: Writer, source: str = "mnpd-calls") -> Generation | None:
    """The active generation, or None before the worker has established one."""
    try:
        return writer.active_generation(source)
    except OperationalError:
        return None


def generation_id(writer: Writer, source: str = "mnpd-calls") -> int:
    """The active generation's id, once the worker has established one."""
    active = generation(writer, source)
    assert active is not None
    return active.id


def state(writer: Writer) -> str:
    return str(status(writer).get("state", ""))
