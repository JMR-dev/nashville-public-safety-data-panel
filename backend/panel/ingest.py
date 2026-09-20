"""ArcGIS transport with a shared, adaptive request budget."""

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from panel.payload import as_dict, as_list

TRANSIENT = {408, 429, 500, 502, 503, 504}
ATTEMPTS = 5
INITIAL_BACKOFF = 2.0
MAX_BACKOFF = 60.0
BACKFILL_RATE = 100.0
LIVE_GAP = 0.5
PAGE_SIZE = 2000


class SourceError(Exception):
    """The upstream source could not supply a trustworthy response."""


class TransientError(SourceError):
    """A response that may succeed if the same request is retried."""


class Pacer:
    """Shared across all requests to a source, including retries.

    Backfill spaces request starts by the current rate. Live mode allows one request at a time
    and waits ``LIVE_GAP`` seconds after each response. A failure pauses every request until its
    retry delay has passed; during backfill it also halves the rate, down to one per second, for
    the rest of the run.
    """

    def __init__(
        self,
        *,
        backfill: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backfill = backfill
        self.sleep = sleep
        self.clock = clock
        self.rate = BACKFILL_RATE
        self.next_start = 0.0
        self.cooldown = 0.0
        self.lock = asyncio.Lock()
        self.serial = asyncio.Lock()

    async def enter(self) -> None:
        if not self.backfill:
            await self.serial.acquire()
        async with self.lock:
            delay = max(self.next_start, self.cooldown) - self.clock()
            if delay > 0:
                await self.sleep(delay)
            self.next_start = self.clock() + 1 / self.rate

    def leave(self) -> None:
        if not self.backfill:
            self.next_start = self.clock() + LIVE_GAP
            self.serial.release()

    def fail(self, delay: float) -> None:
        self.cooldown = max(self.cooldown, self.clock() + delay)
        if self.backfill:
            self.rate = max(1.0, self.rate / 2)


def retry_delay(value: str | None, attempt: int, now: datetime | None = None) -> float:
    """A valid Retry-After wins; otherwise back off exponentially from two seconds.

    Jitter only lengthens the nominal delay, and the result never exceeds the cap.
    """
    if value is not None:
        try:
            delay = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
            except ValueError:
                pass
            else:
                # RFC 5322 "-0000" means UTC with unknown origin; Python parses it as naive.
                moment = date if date.tzinfo else date.replace(tzinfo=UTC)
                return max(0.0, (moment - (now or datetime.now(UTC))).total_seconds())
        else:
            if math.isfinite(delay) and delay >= 0:
                return delay
    nominal = min(MAX_BACKOFF, INITIAL_BACKOFF * 2**attempt)
    return min(MAX_BACKOFF, nominal * random.uniform(1.0, 1.2))


def received_since(epoch_ms: int) -> str:
    """A ``where`` clause for records received at or after a moment (the layer stores UTC)."""
    moment = datetime.fromtimestamp(epoch_ms / 1000, UTC)
    return f"Call_Received >= TIMESTAMP '{moment:%Y-%m-%d %H:%M:%S}'"


def _objectid(value: Any) -> int:
    if type(value) is not int:
        raise SourceError(f"Source returned a non-integer OBJECTID: {value!r}")
    return value


@dataclass(frozen=True)
class Page:
    rows: list[dict[str, Any]]
    exceeded: bool

    @property
    def last(self) -> int | None:
        return self.rows[-1]["OBJECTID"] if self.rows else None


@dataclass(frozen=True)
class LayerInfo:
    name: str | None
    service_item_id: str | None
    max_record_count: int
    fields: list[dict[str, Any]]
    data_edited_at: int | None


class ArcGIS:
    def __init__(self, client: httpx.AsyncClient, url: str, pacer: Pacer) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.pacer = pacer
        self.page_size = PAGE_SIZE

    async def request(self, params: dict[str, str], *, path: str = "/query") -> dict[str, Any]:
        for attempt in range(ATTEMPTS):
            await self.pacer.enter()
            retry_after: str | None = None
            try:
                response = await self.client.get(self.url + path, params={"f": "json", **params})
                retry_after = response.headers.get("retry-after")
                if response.status_code in TRANSIENT:
                    raise TransientError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return self._body(response)
            except TransientError, httpx.TransportError:
                self.pacer.fail(retry_delay(retry_after, attempt))
            except httpx.HTTPStatusError as error:
                raise SourceError(str(error)) from error
            finally:
                self.pacer.leave()
        raise SourceError("Source request failed after five attempts")

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as error:
            raise TransientError("Source returned invalid JSON") from error
        body = as_dict(data)
        if body is None:
            raise TransientError("Source returned JSON that is not an object")
        detail = as_dict(body.get("error"))
        if detail is not None:
            if detail.get("code") in TRANSIENT:
                raise TransientError(str(detail))
            raise SourceError(str(detail))
        return body

    async def metadata(self) -> LayerInfo:
        layer = await self.request({}, path="")
        count = layer.get("maxRecordCount")
        fields = as_list(layer.get("fields"))
        if (
            layer.get("objectIdField") != "OBJECTID"
            or type(count) is not int
            or count < 1
            or fields is None
        ):
            raise SourceError("Unsupported source layer: pagination or schema is not supported")
        editing = as_dict(layer.get("editingInfo")) or {}
        self.page_size = min(PAGE_SIZE, count)
        return LayerInfo(
            name=layer.get("name"),
            service_item_id=layer.get("serviceItemId"),
            max_record_count=count,
            fields=fields,
            data_edited_at=editing.get("dataLastEditDate"),
        )

    async def maximum(self) -> int:
        page = await self._features(
            {
                "where": "1=1",
                "outFields": "OBJECTID",
                "orderByFields": "OBJECTID DESC",
                "resultRecordCount": "1",
                "returnGeometry": "false",
            }
        )
        return page.last or 0

    async def page(self, cursor: int, upper: int | None = None, where: str = "1=1") -> Page:
        """One keyset page of full records after ``cursor``, optionally up to ``upper``."""
        bound = "" if upper is None else f" AND OBJECTID <= {upper}"
        page = await self._features(
            {
                "where": f"OBJECTID > {cursor}{bound} AND ({where})",
                "orderByFields": "OBJECTID ASC",
                "resultRecordCount": str(self.page_size),
                "outFields": "*",
                "returnGeometry": "false",
            }
        )
        ids = [row["OBJECTID"] for row in page.rows]
        limit = math.inf if upper is None else upper
        if ids != sorted(set(ids)) or any(not cursor < oid <= limit for oid in ids):
            raise SourceError("Source returned non-monotonic or out-of-range OBJECTIDs")
        if page.exceeded and not ids:
            raise SourceError("Source reported more records but returned none")
        return page

    async def _features(self, params: dict[str, str]) -> Page:
        data = await self.request(params)
        found = as_list(data.get("features"))
        if found is None:
            raise SourceError("Source response has no feature list")
        rows: list[dict[str, Any]] = []
        for feature in found:
            row = as_dict((as_dict(feature) or {}).get("attributes"))
            if row is None:
                raise SourceError("Source feature has no attributes")
            _objectid(row.get("OBJECTID"))
            rows.append(row)
        return Page(rows, data.get("exceededTransferLimit") is True)


def partition(lower: int, upper: int, parts: int) -> list[tuple[int, int]]:
    """Split ``(lower, upper]`` into at most ``parts`` contiguous, disjoint ranges."""
    width = max(1, math.ceil((upper - lower) / parts))
    return [(start, min(start + width, upper)) for start in range(lower, upper, width)]
