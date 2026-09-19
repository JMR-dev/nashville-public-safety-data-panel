"""ArcGIS transport contracts: deterministic fake time, real HTTP parsing."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from panel.ingest import (
    ArcGIS,
    Pacer,
    SourceError,
    partition,
    received_since,
    retry_delay,
)
from tests.support import call_row, load_fixture

BASE = "https://source.test/FeatureServer/0"


class Clock:
    """Monotonic seconds that advance only through sleeps and simulated latency."""

    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


Handler = Callable[[httpx.Request], httpx.Response]


def source(handler: Handler, pacer: Pacer) -> tuple[ArcGIS, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ArcGIS(client, BASE + "/", pacer), client


def params(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(str(request.url)).query).items()}


def features(*rows: dict[str, Any], exceeded: bool = False) -> dict[str, Any]:
    return {"features": [{"attributes": row} for row in rows], "exceededTransferLimit": exceeded}


async def test_live_gap_follows_every_response_including_empty_ones() -> None:
    clock = Clock()
    starts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        clock.now += 0.2
        return httpx.Response(200, json=features())

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        assert (await arcgis.page(0)).rows == []
        assert (await arcgis.page(0)).rows == []
    assert starts == pytest.approx([0, 0.7])


async def test_backfill_spaces_request_starts_by_the_shared_rate() -> None:
    clock = Clock()
    pacer = Pacer(backfill=True, sleep=clock.sleep, clock=clock.time)
    starts: list[float] = []

    async def start() -> None:
        await pacer.enter()
        starts.append(clock.now)
        pacer.leave()

    await asyncio.gather(*(start() for _ in range(4)))
    assert starts == pytest.approx([0, 0.01, 0.02, 0.03])


async def test_retry_after_is_honored_for_exactly_five_attempts() -> None:
    clock = Clock()
    pacer = Pacer(backfill=True, sleep=clock.sleep, clock=clock.time)
    attempts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        attempts.append(clock.now)
        return httpx.Response(429, headers={"Retry-After": "7"})

    arcgis, client = source(respond, pacer)
    async with client:
        with pytest.raises(SourceError, match="five attempts"):
            await arcgis.page(0, 10)
    assert len(attempts) == 5
    assert all(later - earlier >= 7 for earlier, later in zip(attempts, attempts[1:], strict=False))
    assert pacer.rate == 3.125


def test_retry_after_accepts_seconds_or_an_http_date() -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert retry_delay("12", 0, now) == 12
    assert retry_delay(format_datetime(now + timedelta(seconds=30), usegmt=True), 0, now) == 30
    assert retry_delay(format_datetime(now - timedelta(seconds=30), usegmt=True), 0, now) == 0
    assert retry_delay("Sat, 19 Sep 2026 12:00:45 -0000", 0, now) == 45


def test_invalid_retry_after_falls_back_to_bounded_exponential_backoff() -> None:
    for value in (None, "-1", "nan", "soon"):
        for attempt, nominal in enumerate((2, 4, 8, 16)):
            for _ in range(50):
                assert nominal <= retry_delay(value, attempt) <= nominal * 1.2
    assert {retry_delay(None, 8) for _ in range(20)} == {60}


async def test_ingestion_rate_halves_per_transient_failure_down_to_one_per_second() -> None:
    clock = Clock()
    pacer = Pacer(backfill=True, sleep=clock.sleep, clock=clock.time)
    for _ in range(10):
        pacer.fail(0)
    assert pacer.rate == 1


async def test_live_failures_pause_without_changing_the_rate() -> None:
    clock = Clock()
    pacer = Pacer(sleep=clock.sleep, clock=clock.time)
    pacer.fail(30)
    await pacer.enter()
    pacer.leave()
    assert clock.now == 30
    assert pacer.rate == 100


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(503),
        httpx.Response(200, json={"error": {"code": 504, "message": "Timeout"}}),
        httpx.Response(200, text="<html>truncated"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["http-503", "arcgis-envelope", "invalid-json", "non-object"],
)
async def test_transient_failures_are_retried_until_a_good_response(
    failure: httpx.Response,
) -> None:
    clock = Clock()
    responses = [failure, httpx.Response(200, json=features(call_row(1)))]
    arcgis, client = source(
        lambda request: responses.pop(0), Pacer(sleep=clock.sleep, clock=clock.time)
    )
    async with client:
        page = await arcgis.page(0, 10)
    assert [row["OBJECTID"] for row in page.rows] == [1]
    assert 2 <= clock.delays[0] <= 2.4


async def test_network_errors_are_retried() -> None:
    clock = Clock()
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=features())

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        assert (await arcgis.page(0)).rows == []
    assert calls == 2


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "404"),
        (httpx.Response(200, json={"error": {"code": 400, "message": "bad field"}}), "bad field"),
    ],
    ids=["http-404", "arcgis-400"],
)
async def test_permanent_errors_fail_on_the_first_attempt(
    response: httpx.Response, message: str
) -> None:
    clock = Clock()
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return response

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        with pytest.raises(SourceError, match=message):
            await arcgis.page(0, 10)
    assert attempts == 1


async def test_page_query_uses_keyset_bounds_without_geometry() -> None:
    clock = Clock()
    sent: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(params(request))
        return httpx.Response(200, json=features(call_row(6), call_row(7), exceeded=True))

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        bounded = await arcgis.page(5, 9, where="Tencode = 70")
        open_ended = await arcgis.page(5)
    assert (bounded.exceeded, bounded.last) == (True, 7)
    assert open_ended.last == 7
    assert sent[0] == {
        "f": "json",
        "where": "OBJECTID > 5 AND OBJECTID <= 9 AND (Tencode = 70)",
        "orderByFields": "OBJECTID ASC",
        "resultRecordCount": "2000",
        "outFields": "*",
        "returnGeometry": "false",
    }
    assert sent[1]["where"] == "OBJECTID > 5 AND (1=1)"


UNTRUSTWORTHY_PAGES: dict[str, dict[str, Any]] = {
    "descending": features(call_row(3), call_row(2)),
    "duplicate": features(call_row(2), call_row(2)),
    "above-upper": features(call_row(11)),
    "not-after-cursor": features(call_row(5)),
    "exceeded-but-empty": features(exceeded=True),
    "no-attributes": {"features": [{"geometry": {}}]},
    "string-id": {"features": [{"attributes": {"OBJECTID": "7"}}]},
    "boolean-id": {"features": [{"attributes": {"OBJECTID": True}}]},
    "no-features": {"objectIds": [1]},
}


@pytest.mark.parametrize("body", list(UNTRUSTWORTHY_PAGES.values()), ids=list(UNTRUSTWORTHY_PAGES))
async def test_untrustworthy_pages_are_rejected(body: dict[str, Any]) -> None:
    clock = Clock()
    arcgis, client = source(
        lambda request: httpx.Response(200, json=body), Pacer(sleep=clock.sleep, clock=clock.time)
    )
    async with client:
        with pytest.raises(SourceError):
            await arcgis.page(5, 10)


async def test_metadata_describes_the_layer_and_sets_the_page_size() -> None:
    clock = Clock()
    layer = load_fixture("layer_metadata.json")
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=layer)

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        info = await arcgis.metadata()
    assert sent[0].url.path == "/FeatureServer/0"
    assert info.name == "MNPD_Calls_for_Service"
    assert info.service_item_id == "f8b1bdf0612647cfb9c9ccce31ac411a"
    assert info.data_edited_at == 1789811893071
    assert info.max_record_count == 2000
    assert [field["name"] for field in info.fields][:2] == ["OBJECTID", "Event_Number"]
    assert arcgis.page_size == 2000


@pytest.mark.parametrize(("advertised", "page_size"), [(1000, 1000), (16000, 2000)])
async def test_page_size_defaults_to_2000_within_the_advertised_limit(
    advertised: int, page_size: int
) -> None:
    clock = Clock()
    layer = {**load_fixture("layer_metadata.json"), "maxRecordCount": advertised}
    del layer["editingInfo"]
    arcgis, client = source(
        lambda request: httpx.Response(200, json=layer), Pacer(sleep=clock.sleep, clock=clock.time)
    )
    async with client:
        info = await arcgis.metadata()
    assert arcgis.page_size == page_size
    assert info.data_edited_at is None


@pytest.mark.parametrize(
    "change",
    [
        {"objectIdField": "FID"},
        {"maxRecordCount": 0},
        {"maxRecordCount": "many"},
        {"fields": "OBJECTID"},
    ],
    ids=["other-id-field", "zero-page", "non-numeric-page", "fields-not-a-list"],
)
async def test_unsupported_layers_are_rejected(change: dict[str, Any]) -> None:
    clock = Clock()
    layer = {**load_fixture("layer_metadata.json"), **change}
    arcgis, client = source(
        lambda request: httpx.Response(200, json=layer), Pacer(sleep=clock.sleep, clock=clock.time)
    )
    async with client:
        with pytest.raises(SourceError, match="Unsupported"):
            await arcgis.metadata()


async def test_maximum_reads_the_highest_objectid() -> None:
    clock = Clock()
    bodies = [features(call_row(365978)), features()]
    sent: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(params(request))
        return httpx.Response(200, json=bodies.pop(0))

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        assert await arcgis.maximum() == 365978
        assert await arcgis.maximum() == 0
    assert sent[0]["orderByFields"] == "OBJECTID DESC"
    assert sent[0]["resultRecordCount"] == "1"


async def test_ids_lists_matching_objectids() -> None:
    clock = Clock()
    bodies: list[dict[str, Any]] = [
        {"objectIdFieldName": "OBJECTID", "objectIds": [9, 3]},
        {"objectIdFieldName": "OBJECTID", "objectIds": None},
        {"objectIdFieldName": "OBJECTID", "objectIds": ["3"]},
        {"objectIdFieldName": "OBJECTID", "objectIds": "3"},
    ]
    sent: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(params(request))
        return httpx.Response(200, json=bodies.pop(0))

    arcgis, client = source(respond, Pacer(sleep=clock.sleep, clock=clock.time))
    async with client:
        assert await arcgis.ids([3, 9, 12]) == {3, 9}
        assert await arcgis.ids([4]) == set()
        with pytest.raises(SourceError):
            await arcgis.ids([3])
        with pytest.raises(SourceError, match="no OBJECTID list"):
            await arcgis.ids([3])
    assert sent[0]["where"] == "OBJECTID IN (3,9,12)"
    assert sent[0]["returnIdsOnly"] == "true"


def test_received_since_uses_the_layers_utc_timestamp_syntax() -> None:
    moment = int(datetime(2026, 9, 17, 5, 30, 15, tzinfo=UTC).timestamp() * 1000)
    assert received_since(moment) == "Call_Received >= TIMESTAMP '2026-09-17 05:30:15'"


def test_partition_covers_the_boundary_with_disjoint_ranges() -> None:
    assert partition(0, 0, 32) == []
    assert partition(0, 3, 32) == [(0, 1), (1, 2), (2, 3)]
    ranges = partition(10, 100, 4)
    assert ranges[0][0] == 10
    assert ranges[-1][1] == 100
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False))
