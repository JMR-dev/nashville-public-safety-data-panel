"""A simulated ArcGIS feature layer.

It answers the queries this project sends and rejects anything else, so the worker is exercised
against the source's real shape: keyset pages bounded by OBJECTID, a received-time filter for
reconciliation, transfer-limit flags, id-only queries, and scripted failures.
"""

import json
import re
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from tests.support import call_row, load_fixture

BOUND = re.compile(r"OBJECTID > (\d+)")
UPPER = re.compile(r"OBJECTID <= (\d+)")
RECEIVED = re.compile(r"Call_Received >= TIMESTAMP '([\d-]{10} [\d:]{8})'")
IDS = re.compile(r"OBJECTID IN \(([\d,]+)\)")
EVERYTHING = re.compile(r"^(1=1|\(1=1\))$")

Predicate = Callable[[str], Callable[[int], bool]]


class FakeSource:
    def __init__(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]],
        now: Callable[[], int] | None = None,
        latency: float = 0.05,
        service_item_id: str = "item-1",
        max_record_count: int = 2000,
    ) -> None:
        self.records: dict[int, dict[str, Any]] = {}
        self.fields: list[dict[str, Any]] = load_fixture("layer_metadata.json")["fields"]
        self.service_item_id = service_item_id
        self.max_record_count = max_record_count
        self.data_edited_at = 1_789_000_000_000
        self.sleep = sleep
        self.now = now or (lambda: 1_789_819_200_000)
        self.latency = latency
        self.requests: list[dict[str, str]] = []
        self.headers: list[httpx.Headers] = []
        self.starts: list[float] = []
        self.epochs: list[int] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        # Responses returned instead of real ones, oldest first.
        self.failures: deque[httpx.Response] = deque()
        self.page_failures: deque[httpx.Response] = deque()
        self.pages_before_failing = 0
        self.pages_served = 0

    # Dataset

    def publish(self, count: int, *, first: int = 1, received: int | None = None) -> None:
        """Append records the way the source does, one OBJECTID after another.

        Without an explicit time, records are spread over the day before now, the way a day of
        calls arrives, so they all fall inside the window reconciliation covers.
        """
        day = 86_400_000
        start = received if received is not None else self.now() - day
        step = day // count if received is None else 60_000
        for offset in range(count):
            oid = first + offset
            self.records[oid] = call_row(oid, Call_Received=start + offset * step)

    def edit(self, oid: int, **changes: Any) -> None:
        self.records[oid] = {**self.records[oid], **changes}

    def delete(self, *oids: int) -> None:
        for oid in oids:
            del self.records[oid]

    def replace(self, service_item_id: str) -> None:
        """A new published service: the identifiers start over."""
        self.records.clear()
        self.service_item_id = service_item_id

    def fail(self, *responses: httpx.Response) -> None:
        """Answer the next requests with these responses, whatever they ask for."""
        self.failures.extend(responses)

    def fail_pages(self, *responses: httpx.Response, after: int = 0) -> None:
        """Answer record pages with these responses once ``after`` pages have been served."""
        self.pages_before_failing = after
        self.page_failures.extend(responses)

    def drop_field(self, name: str) -> None:
        self.fields = [field for field in self.fields if field["name"] != name]

    # Transport

    def transport(self, clock: Callable[[], float]) -> httpx.MockTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            self.starts.append(clock())
            self.epochs.append(self.now())
            self.headers.append(request.headers)
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            try:
                await self.sleep(self.latency)
                return self.respond(request)
            finally:
                self.in_flight -= 1

        return httpx.MockTransport(handle)

    def respond(self, request: httpx.Request) -> httpx.Response:
        params = {key: value for key, value in request.url.params.items()}
        self.requests.append(params)
        if self.failures:
            return self.failures.popleft()
        if not request.url.path.endswith("/query"):
            return httpx.Response(200, json=self.metadata())
        return self.query(params)

    def metadata(self) -> dict[str, Any]:
        return {
            "name": "MNPD_Calls_for_Service",
            "type": "Feature Layer",
            "serviceItemId": self.service_item_id,
            "objectIdField": "OBJECTID",
            "maxRecordCount": self.max_record_count,
            "fields": self.fields,
            "editingInfo": {"dataLastEditDate": self.data_edited_at},
        }

    def query(self, params: dict[str, str]) -> httpx.Response:
        if params.get("outFields") == "*":
            self.pages_served += 1
            if self.page_failures and self.pages_served > self.pages_before_failing:
                return self.page_failures.popleft()
        selected = self.select(params.get("where", ""))
        if selected is None:
            message = {"code": 400, "message": f"Unsupported where clause: {params.get('where')}"}
            return httpx.Response(200, json={"error": message})
        if params.get("returnIdsOnly") == "true":
            return httpx.Response(
                200, json={"objectIdFieldName": "OBJECTID", "objectIds": sorted(selected)}
            )
        descending = params.get("orderByFields", "").endswith("DESC")
        ordered = sorted(selected, reverse=descending)
        limit = int(params.get("resultRecordCount", str(self.max_record_count)))
        page = ordered[:limit]
        return httpx.Response(
            200,
            json={
                "objectIdFieldName": "OBJECTID",
                "features": [{"attributes": self.records[oid]} for oid in page],
                "exceededTransferLimit": len(ordered) > len(page),
            },
        )

    def select(self, where: str) -> list[int] | None:
        """OBJECTIDs matching a where clause, or None if this layer does not support it."""
        builders: tuple[tuple[re.Pattern[str], Predicate], ...] = (
            (BOUND, self.after),
            (UPPER, self.up_to),
            (IDS, self.among),
            (RECEIVED, self.received_after),
        )
        remaining = where
        tests: list[Callable[[int], bool]] = []
        for pattern, build in builders:
            found = pattern.search(remaining)
            if found is not None:
                tests.append(build(found.group(1)))
                remaining = remaining.replace(found.group(0), "", 1)
        remaining = remaining.replace("AND", " ").replace("()", "").strip()
        if remaining and not EVERYTHING.match(remaining):
            return None
        return [oid for oid in self.records if all(test(oid) for test in tests)]

    @staticmethod
    def after(value: str) -> Callable[[int], bool]:
        lower = int(value)
        return lambda oid: oid > lower

    @staticmethod
    def up_to(value: str) -> Callable[[int], bool]:
        upper = int(value)
        return lambda oid: oid <= upper

    @staticmethod
    def among(value: str) -> Callable[[int], bool]:
        listed = {int(part) for part in value.split(",")}
        return lambda oid: oid in listed

    def received_after(self, timestamp: str) -> Callable[[int], bool]:
        moment = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        epoch_ms = int(moment.timestamp() * 1000)

        def test(oid: int) -> bool:
            received = self.records[oid].get("Call_Received")
            return received is not None and received >= epoch_ms

        return test


def error_response(code: int, message: str = "Service busy") -> httpx.Response:
    """An ArcGIS error envelope, which the source returns with HTTP 200."""
    return httpx.Response(200, json={"error": {"code": code, "message": message}})


def unavailable(retry_after: str | None = None) -> httpx.Response:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(503, headers=headers)


def received_at(rows: Iterable[dict[str, Any]]) -> Sequence[int]:
    return [row["Call_Received"] for row in rows]


def as_json(response: httpx.Response) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(response.content)
    return parsed
