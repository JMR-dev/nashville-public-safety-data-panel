"""A fixture-backed database and HTTP client for API tests."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from panel.api.app import create_app
from panel.settings import Settings
from panel.store import Progress, Provenance, SourceState, Writer
from tests.support import SOURCE, URL, ManualClock, call_row

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
T0 = 1_789_819_200_000  # 2026-09-19T12:00:00Z
HOUR = 3_600_000
PROVENANCE = Provenance(url=URL, service_item_id="item-1", layer_name="MNPD_Calls_for_Service")


def iso(epoch_ms: int) -> str:
    return (EPOCH + timedelta(milliseconds=epoch_ms)).isoformat()


WINDOW: dict[str, Any] = {"since": iso(T0 - 24 * HOUR), "until": iso(T0)}

ROWS = [
    call_row(
        1,
        Call_Received=T0 - HOUR,
        ZONE_="15",
        Sector="C1",
        Tencode_Description="ALARM - BURGLAR",
        Disposition_Description="ASSISTED CITIZEN",
    ),
    call_row(
        2,
        Call_Received=T0 - 2 * HOUR,
        ZONE_="23",
        Sector="N2",
        Tencode_Description="TRAFFIC VIOLATION",
        Disposition_Description="REPORT TAKEN",
        Latitude=None,
        Longitude=None,
    ),
    call_row(
        3,
        Call_Received=T0 - 3 * HOUR,
        ZONE_="15",
        Sector="C1",
        Tencode_Description="ALARM - BURGLAR",
        Disposition_Description=None,
        Latitude=0.0,
        Longitude=0.0,
    ),
    call_row(4, Call_Received=T0 - 30 * HOUR, ZONE_="15", Tencode_Description="SHOTS FIRED"),
    call_row(
        5,
        Call_Received=T0 - HOUR,
        ZONE_="31",
        Sector=None,
        Tencode_Description="3",
        Disposition_Description="ASSISTED CITIZEN",
        Priority="HIGH",
    ),
    call_row(6, Call_Received=None),
]


def populate(database: Path, clock: ManualClock) -> int:
    """Two committed pages (data versions 1 and 2) under one generation; returns its id."""
    writer = Writer(database, clock=clock)
    writer.initialize()
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.capture_boundary(generation, 6, [(0, 3), (3, 6)])
    writer.commit_page(generation, ROWS[:3], Progress("backfill:0-3", cursor=3, complete=True))
    writer.commit_page(generation, ROWS[3:], Progress("backfill:3-6", cursor=5))
    writer.set_status(
        SOURCE,
        generation=generation,
        state=SourceState.BACKFILLING,
        detail="Backfilling 2 ranges",
        upstream_edit_at=T0 - 5 * 60_000,
    )
    writer.close()
    return generation


def settings_for(database: Path, **overrides: Any) -> Settings:
    return Settings(database=database, **overrides)


@asynccontextmanager
async def api_client(settings: Settings, clock: ManualClock) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(settings, clock=clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as client:
        yield client


async def graphql(
    client: httpx.AsyncClient, query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert response.status_code == 200, response.text
    return response.json()


async def data(
    client: httpx.AsyncClient, query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = await graphql(client, query, variables)
    assert "errors" not in body, body
    return body["data"]


async def error_messages(
    client: httpx.AsyncClient, query: str, variables: dict[str, Any] | None = None
) -> list[str]:
    body = await graphql(client, query, variables)
    return [error["message"] for error in body["errors"]]
