"""The public, read-only HTTP API: GraphQL, data-version events, and health checks."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from strawberry.fastapi import GraphQLRouter

from panel.api import queries
from panel.api.events import VersionFeed, events_router
from panel.api.schema import Context, build_schema
from panel.api.snapshot import Snapshot
from panel.database import current_revision, head_revision, open_engine
from panel.settings import Settings
from panel.store import Clock, system_clock


def create_app(settings: Settings, *, clock: Clock = system_clock) -> FastAPI:
    engine = open_engine(settings.database)

    def read_version() -> int:
        with engine.connect() as connection:
            return queries.data_version(connection)

    async def read_version_async() -> int:
        return await asyncio.to_thread(read_version)

    feed = VersionFeed(
        read_version_async,
        poll=settings.event_poll_seconds,
        interval=settings.event_min_interval_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        watcher = asyncio.create_task(feed.run())
        try:
            yield
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            engine.dispose()

    async def context() -> AsyncGenerator[Context]:
        snapshot = Snapshot(engine)
        try:
            yield Context(snapshot, settings, clock)
        finally:
            await snapshot.close()

    app = FastAPI(
        title="Nashville Public Safety Panel",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    graphql = GraphQLRouter(
        build_schema(settings),
        context_getter=context,
        graphql_ide=None,
        allow_queries_via_get=True,
        subscription_protocols=(),
    )
    app.include_router(graphql, prefix="/graphql")
    app.include_router(events_router(feed))

    @app.get("/healthz")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    def revision() -> str | None:
        with engine.connect() as connection:
            return current_revision(connection)

    @app.get("/readyz")
    async def readiness() -> JSONResponse:
        expected = head_revision()
        try:
            found = await asyncio.to_thread(revision)
        except SQLAlchemyError:
            return unavailable("The database cannot be read")
        if found is None:
            return unavailable("The database has not been migrated")
        if found != expected:
            return unavailable(f"The database is at revision {found}, not {expected}")
        return JSONResponse({"status": "ready", "revision": found})

    return app


def unavailable(reason: str) -> JSONResponse:
    return JSONResponse({"status": "unavailable", "reason": reason}, status_code=503)
