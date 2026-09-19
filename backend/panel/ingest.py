"""ArcGIS transport with a shared, adaptive request budget."""

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

TRANSIENT = {408, 429, 500, 502, 503, 504}


class SourceError(Exception):
    """The upstream source could not supply a trustworthy response."""


class Pacer:
    """Shared across all requests to a source, including retries."""

    def __init__(
        self,
        *,
        backfill: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.backfill = backfill
        self.sleep = sleep
        self.clock = clock
        self.rate = 100.0
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
            self.next_start = self.clock() + 0.5
            self.serial.release()

    def fail(self, delay: float) -> None:
        self.cooldown = max(self.cooldown, self.clock() + delay)
        if self.backfill:
            self.rate = max(1.0, self.rate / 2)


def retry_delay(value: str | None, attempt: int, now: datetime | None = None) -> float:
    """Retry-After is a lower bound, never capped by our backoff limit."""
    if value is not None:
        try:
            delay = float(value)
            if math.isfinite(delay) and delay >= 0:
                return delay
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                return max(0.0, (date - (now or datetime.now(UTC))).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    base = min(60.0, 2.0 * 2**attempt)
    return random.uniform(base / 2, base)  # jitter does not exceed the cap


class ArcGIS:
    def __init__(self, client: httpx.AsyncClient, url: str, pacer: Pacer):
        self.client = client
        self.url = url.rstrip("/")
        self.pacer = pacer
        self.page_size = 2000

    async def request(self, params: dict[str, str], *, metadata: bool = False) -> dict[str, Any]:
        for attempt in range(5):
            await self.pacer.enter()
            try:
                endpoint = self.url if metadata else self.url + "/query"
                response = await self.client.get(endpoint, params={"f": "json", **params})
                if response.status_code in TRANSIENT:
                    self.pacer.fail(retry_delay(response.headers.get("retry-after"), attempt))
                    continue
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                if "error" in data:
                    error = data["error"]
                    if error.get("code") in TRANSIENT:
                        self.pacer.fail(retry_delay(response.headers.get("retry-after"), attempt))
                        continue
                    raise SourceError(str(error))
                return data
            except httpx.TransportError:
                self.pacer.fail(retry_delay(None, attempt))
            except (httpx.HTTPStatusError, ValueError) as error:
                raise SourceError(str(error)) from error
            finally:
                self.pacer.leave()
        raise SourceError("Source request failed after five attempts")

    async def metadata(self) -> dict[str, Any]:
        result = await self.request({}, metadata=True)
        self.page_size = min(2000, int(result["maxRecordCount"]))
        if self.page_size < 1 or result["objectIdField"] != "OBJECTID":
            raise SourceError("Unsupported source pagination schema")
        return result

    async def maximum(self) -> int:
        data = await self.request({
            "where": "1=1", "outFields": "OBJECTID", "orderByFields": "OBJECTID DESC",
            "resultRecordCount": "1", "returnGeometry": "false",
        })
        rows = data["features"]
        return int(rows[0]["attributes"]["OBJECTID"]) if rows else 0

    async def page(self, cursor: int, upper: int, where: str = "1=1") -> list[dict[str, Any]]:
        data = await self.request({
            "where": f"OBJECTID > {cursor} AND OBJECTID <= {upper} AND ({where})",
            "orderByFields": "OBJECTID ASC", "resultRecordCount": str(self.page_size),
            "outFields": "*", "returnGeometry": "false",
        })
        rows = [feature["attributes"] for feature in data["features"]]
        ids = [int(row["OBJECTID"]) for row in rows]
        if ids != sorted(set(ids)) or any(not cursor < oid <= upper for oid in ids):
            raise SourceError("Source returned non-monotonic or out-of-range OBJECTIDs")
        return rows


def partition(lower: int, upper: int, workers: int) -> list[tuple[int, int]]:
    width = max(1, math.ceil((upper - lower) / workers))
    return [(start, min(start + width, upper)) for start in range(lower, upper, width)]
