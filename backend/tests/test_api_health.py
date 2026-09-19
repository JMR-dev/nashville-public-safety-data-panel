"""Liveness reports the process; readiness reports whether retained data can be served."""

from pathlib import Path

from sqlalchemy import text

from panel.database import open_engine
from panel.store import Writer
from tests.api_support import api_client, settings_for
from tests.support import ManualClock


async def test_liveness_does_not_depend_on_the_database(tmp_path: Path, clock: ManualClock) -> None:
    async with api_client(settings_for(tmp_path / "absent" / "panel.sqlite"), clock) as client:
        response = await client.get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_readiness_fails_when_the_database_cannot_be_read(
    unreadable_database: Path, clock: ManualClock
) -> None:
    async with api_client(settings_for(unreadable_database), clock) as client:
        response = await client.get("/readyz")
    assert (response.status_code, response.json()) == (
        503,
        {"status": "unavailable", "reason": "The database cannot be read"},
    )


async def test_readiness_requires_the_current_migration_revision(
    empty_database: Path, clock: ManualClock
) -> None:
    async with api_client(settings_for(empty_database), clock) as client:
        unmigrated = await client.get("/readyz")

        writer = Writer(empty_database, clock=clock)
        writer.initialize()
        writer.close()
        ready = await client.get("/readyz")

        engine = open_engine(empty_database, writer=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0000'"))
        engine.dispose()
        behind = await client.get("/readyz")

    unavailable = {"status": "unavailable"}
    assert (unmigrated.status_code, unmigrated.json()) == (
        503,
        {**unavailable, "reason": "The database has not been migrated"},
    )
    assert (ready.status_code, ready.json()) == (200, {"status": "ready", "revision": "0001"})
    assert (behind.status_code, behind.json()) == (
        503,
        {**unavailable, "reason": "The database is at revision 0000, not 0001"},
    )
