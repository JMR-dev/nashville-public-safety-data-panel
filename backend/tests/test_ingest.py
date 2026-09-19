"""Source ingestion contracts: deterministic fake time, real HTTP parsing."""

import httpx
import pytest

from panel.ingest import ArcGIS, Pacer, SourceError, partition


class Clock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    async def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds

    def time(self):
        return self.now


@pytest.mark.asyncio
async def test_live_gap_applies_after_response_including_empty():
    clock = Clock()
    pacer = Pacer(sleep=clock.sleep, clock=clock.time)
    requests = []

    def respond(request):
        requests.append(clock.now)
        clock.now += 0.2
        return httpx.Response(200, json={"features": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        source = ArcGIS(client, "https://source.test/0", pacer)
        assert await source.page(0, 10) == []
        assert await source.page(0, 10) == []
    assert requests == pytest.approx([0, 0.7])


@pytest.mark.asyncio
async def test_retry_after_and_five_total_attempts():
    clock = Clock()
    pacer = Pacer(backfill=True, sleep=clock.sleep, clock=clock.time)
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(429, headers={"Retry-After": "7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        source = ArcGIS(client, "https://source.test/0", pacer)
        with pytest.raises(SourceError, match="five attempts"):
            await source.page(0, 10)
    assert count == 5
    assert pacer.rate == 3.125
    assert sum(clock.delays) >= 28


@pytest.mark.asyncio
async def test_json_error_is_not_an_empty_page():
    clock = Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"error": {"code": 400, "message": "bad field"}})
    )) as client:
        source = ArcGIS(client, "https://source.test/0", Pacer(sleep=clock.sleep))
        with pytest.raises(SourceError, match="bad field"):
            await source.page(0, 10)


def test_partition_handles_gaps_and_small_sources():
    assert partition(0, 0, 32) == []
    assert partition(0, 3, 32) == [(0, 1), (1, 2), (2, 3)]
    ranges = partition(10, 100, 4)
    assert ranges[0][0] == 10
    assert ranges[-1][1] == 100
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))
