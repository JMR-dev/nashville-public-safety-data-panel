"""The ingestion worker: backfill, live polling, reconciliation, and honest source status.

One worker owns the database's writer lock. It backfills a captured OBJECTID boundary through
disjoint resumable ranges, then polls continuously, giving live polling priority and fitting
reconciliation into the same paced request stream. Browser activity never reaches the source:
this worker is the only thing that talks to it.

Writes are bounded by construction: each range waits for its own page to be committed before
fetching the next, so no more pages are in flight than the configured concurrency allows.
"""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from panel.ingest import ArcGIS, LayerInfo, Pacer, SourceError, partition, received_since
from panel.schema import check_fields, check_rows
from panel.settings import Settings
from panel.store import (
    Clock,
    Generation,
    Progress,
    Provenance,
    RangeCheckpoint,
    SourceState,
    Writer,
    range_name,
    system_clock,
)

log = logging.getLogger(__name__)

# Nightly reconciliation is scheduled in the city's own time.
CHICAGO = ZoneInfo("America/Chicago")
# Each backfill range covers several pages, so ranges outnumber workers without being tiny.
PAGES_PER_RANGE = 8
LIVE = "live"


class SchemaConflict(Exception):
    """The source's published schema no longer matches what this project stores."""

    def __init__(self, problems: Sequence[str], generation: int) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)
        self.generation = generation


@dataclass
class Reconciliation:
    """One pass over a scope, collecting the OBJECTIDs the source still publishes."""

    kind: str
    upper: int
    since: int | None
    cursor: int = 0
    seen: set[int] = field(default_factory=set[int])

    @property
    def where(self) -> str:
        return "1=1" if self.since is None else received_since(self.since)


class Worker:
    def __init__(
        self,
        settings: Settings,
        writer: Writer,
        client: httpx.AsyncClient,
        *,
        clock: Clock = system_clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.writer = writer
        self.client = client
        self.now = clock
        self.sleep = sleep
        self.monotonic = monotonic
        self.live_pacer = Pacer(sleep=sleep, clock=monotonic)
        self.backfill_pacer = Pacer(backfill=True, sleep=sleep, clock=monotonic)
        self.source = ArcGIS(client, settings.source_url, self.live_pacer)
        self.bulk = ArcGIS(client, settings.source_url, self.backfill_pacer)
        self._writes = asyncio.Lock()
        self._stop = asyncio.Event()
        self._generation: Generation | None = None
        self._catching_up = False
        self._checked_at: float | None = None
        self._pass: Reconciliation | None = None
        self._window_at: float | None = None
        self._full_at: int | None = None

    async def close(self) -> None:
        await self.client.aclose()
        self.writer.close()

    # Main loop

    async def run(self, stop: asyncio.Event | None = None) -> None:
        self._stop = stop if stop is not None else asyncio.Event()
        self.writer.lock()
        self.writer.initialize()
        await self._status(state=SourceState.STARTING)
        while not self._stop.is_set():
            try:
                await self._cycle()
            except SchemaConflict as conflict:
                await self._block(conflict)
            except SourceError as failure:
                await self._degrade(failure)

    async def _cycle(self) -> None:
        generation = self._generation
        if generation is None or self._due_for_metadata():
            generation = await self._check_source()
            self._generation = generation
        if generation.boundary is None:
            generation = await self._capture_boundary(generation)
        if generation.backfill_completed_at is None:
            await self._backfill(generation)
            return
        await self._poll(generation)
        if not self._catching_up:
            await self._reconcile(generation)

    # The source's metadata, schema, and identity

    def _due_for_metadata(self) -> bool:
        checked = self._checked_at
        return (
            checked is None or self.monotonic() - checked >= self.settings.window_interval_seconds
        )

    async def _check_source(self) -> Generation:
        info = await self.source.metadata()
        self.bulk.page_size = self.source.page_size
        generation = self._resolve(info)
        schema = check_fields(info.fields)
        await self._write(
            self.writer.record_schema,
            generation.id,
            info.fields,
            compatible=schema.compatible,
            problems=schema.problems,
        )
        await self._status(generation=generation.id, upstream_edit_at=info.data_edited_at)
        self._checked_at = self.monotonic()
        if not schema.compatible:
            raise SchemaConflict(schema.problems, generation.id)
        return await self._check_identifiers(generation)

    def _resolve(self, info: LayerInfo) -> Generation:
        """The generation for this published service, starting a new one when it is replaced."""
        active = self.writer.active_generation(self.settings.source)
        if active is None:
            return self._start(info, "initial")
        if active.service_item_id != info.service_item_id:
            log.info("Source service changed; starting a new generation")
            return self._start(info, "rollover")
        return active

    def _start(self, info: LayerInfo, reason: str) -> Generation:
        provenance = Provenance(
            url=self.settings.source_url,
            service_item_id=info.service_item_id,
            layer_name=info.name,
        )
        generation = self.writer.start_generation(self.settings.source, provenance, reason)
        self._pass = None
        self._full_at = None
        self._window_at = None
        return generation

    async def _check_identifiers(self, generation: Generation) -> Generation:
        """A dataset whose highest OBJECTID fell below ours was replaced, not extended."""
        if generation.backfill_completed_at is None:
            return generation
        cursor = self.writer.checkpoint(generation.id, LIVE) or 0
        highest = await self.source.maximum()
        if highest >= cursor:
            return generation
        log.info("Source identifiers restarted; starting a new generation")
        info = LayerInfo(
            name=generation.layer_name,
            service_item_id=generation.service_item_id,
            max_record_count=self.source.page_size,
            fields=[],
            data_edited_at=None,
        )
        return self._start(info, "identifier_reset")

    # Backfill

    async def _capture_boundary(self, generation: Generation) -> Generation:
        boundary = await self.source.maximum()
        width = self.source.page_size * PAGES_PER_RANGE
        parts = max(1, math.ceil(boundary / width))
        await self._write(
            self.writer.capture_boundary, generation.id, boundary, partition(0, boundary, parts)
        )
        refreshed = self.writer.generation(generation.id)
        self._generation = refreshed
        return refreshed

    async def _backfill(self, generation: Generation) -> None:
        pending = [entry for entry in self.writer.ranges(generation.id) if not entry.completed]
        await self._status(
            state=SourceState.BACKFILLING,
            detail=f"Collecting history: {len(pending)} ranges remaining",
        )
        room = asyncio.Semaphore(self.settings.backfill_concurrency)
        ranges = [
            asyncio.create_task(self._backfill_range(generation, entry, room)) for entry in pending
        ]
        try:
            # The first failure stops the run; the pages already committed are kept.
            await asyncio.gather(*ranges)
        finally:
            for task in ranges:
                task.cancel()
            await asyncio.gather(*ranges, return_exceptions=True)
        # A stopped worker leaves ranges unfinished; the boundary completes only when they all do.
        if await self._write(self.writer.complete_backfill, generation.id):
            self._generation = self.writer.generation(generation.id)
            self._window_at = self.monotonic()
            self._full_at = self.now()
            await self._recovered()

    async def _backfill_range(
        self, generation: Generation, entry: RangeCheckpoint, room: asyncio.Semaphore
    ) -> None:
        cursor = entry.cursor
        while not self._stop.is_set():
            async with room:
                page = await self.bulk.page(cursor, entry.upper)
            self._verify(page.rows, generation.id)
            cursor = page.last or cursor
            complete = not page.exceeded
            progress = Progress(
                range_name(entry.lower, entry.upper),
                cursor=entry.upper if complete else cursor,
                complete=complete,
            )
            await self._write(self.writer.commit_page, generation.id, page.rows, progress)
            if complete:
                return

    # Live polling

    async def _poll(self, generation: Generation) -> None:
        cursor = self.writer.checkpoint(generation.id, LIVE) or 0
        page = await self.source.page(cursor)
        self._verify(page.rows, generation.id)
        progress = Progress(LIVE, cursor=page.last or cursor)
        await self._write(self.writer.commit_page, generation.id, page.rows, progress)
        self._catching_up = page.exceeded
        await self._recovered()

    async def _recovered(self) -> None:
        """Live again: the status must not still advertise a problem or a retry time."""
        await self._status(state=SourceState.LIVE, detail=None, degraded_since=None, retry_at=None)

    # Reconciliation

    async def _reconcile(self, generation: Generation) -> None:
        current = self._pass or self._due_pass(generation)
        if current is None:
            return
        self._pass = current
        page = await self.source.page(current.cursor, current.upper, current.where)
        self._verify(page.rows, generation.id)
        current.seen.update(int(row["OBJECTID"]) for row in page.rows)
        current.cursor = page.last or current.cursor
        await self._write(self.writer.commit_page, generation.id, page.rows, None)
        if not page.exceeded:
            await self._finish(generation, current)

    def _due_pass(self, generation: Generation) -> Reconciliation | None:
        upper = self.writer.checkpoint(generation.id, LIVE) or 0
        window = self._window_at
        if window is None or self.monotonic() - window >= self.settings.window_interval_seconds:
            since = self.now() - self.settings.window_hours * 3_600_000
            return Reconciliation("window", upper=upper, since=since)
        if self._nightly_due():
            return Reconciliation("full", upper=upper, since=None)
        return None

    def _nightly_due(self) -> bool:
        local = datetime.fromtimestamp(self.now() / 1000, CHICAGO)
        if local.hour < self.settings.nightly_hour:
            return False
        hour = local.replace(hour=self.settings.nightly_hour, minute=0, second=0, microsecond=0)
        return self._full_at is None or self._full_at < int(hour.timestamp() * 1000)

    async def _finish(self, generation: Generation, current: Reconciliation) -> None:
        """Infer removals only after a complete pass over the scope."""
        present = self.writer.present_ids(generation.id, upper=current.upper, since=current.since)
        missing = present - current.seen
        if missing:
            await self._write(self.writer.mark_removed, generation.id, missing)
        now = self.now()
        if current.kind == "window":
            self._window_at = self.monotonic()
            await self._status(window_reconciled_at=now)
        else:
            self._full_at = now
            await self._status(full_reconciled_at=now)
        self._pass = None

    # Failures

    def _verify(self, rows: Iterable[dict[str, Any]], generation: int) -> None:
        problems = check_rows(rows)
        if problems:
            raise SchemaConflict(problems, generation)

    async def _block(self, conflict: SchemaConflict) -> None:
        """Stop ingesting and keep checking, until the source's schema is compatible again."""
        self._pass = None
        self._checked_at = None
        log.warning("Source schema is incompatible: %s", conflict)
        await self._write(
            self.writer.record_schema,
            conflict.generation,
            [],
            compatible=False,
            problems=conflict.problems,
        )
        await self._status(state=SourceState.SCHEMA_INCOMPATIBLE, detail=str(conflict))
        await self._pause(self.settings.window_interval_seconds)

    async def _degrade(self, failure: SourceError) -> None:
        """Keep the collected data, report the problem, and wait before trying again."""
        wait = self.settings.degraded_wait_seconds
        now = self.now()
        self._pass = None
        self._catching_up = False
        log.warning("Source is not answering: %s", failure)
        await self._status(
            state=SourceState.DEGRADED,
            detail=str(failure),
            degraded_since=now,
            retry_at=now + round(wait * 1000),
        )
        await self._pause(wait)

    async def _waited(self, seconds: float) -> None:
        await self.sleep(seconds)

    async def _pause(self, seconds: float) -> None:
        """Wait, but return at once when the worker is asked to stop."""
        waiting: asyncio.Task[None] = asyncio.create_task(self._waited(seconds))
        stopping: asyncio.Task[bool] = asyncio.create_task(self._stop.wait())
        await asyncio.wait([waiting, stopping], return_when=asyncio.FIRST_COMPLETED)
        waiting.cancel()
        stopping.cancel()

    # Writes

    async def _write[T](self, call: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Every database write goes through one lock, so there is a single writer."""
        async with self._writes:
            return await asyncio.to_thread(call, *args, **kwargs)

    async def _status(self, **changes: Any) -> None:
        await self._write(self.writer.set_status, self.settings.source, **changes)
