"""A fixture-backed API to run the Bruno collections and the Playwright flows against.

The records are the ones the API's own tests use, so every suite asserts against one dataset.
Three services are served, each on its own port:

* the API, on a populated database;
* the same API on a database that was never migrated, which is what an unready deployment
  looks like to ``/readyz``;
* a control service, which reads the API's event stream over HTTP and reports the response it
  got, because the stream never ends and the Bruno CLI cannot read a response that never ends,
  and which publishes another call on request, and takes the published calls back again, so
  the Playwright flows watch a live update arrive the way a browser would and still start from
  the same dataset whichever order they run in.

The API reports a fixed clock as its own time, and the records are timestamped by that same
clock, so the dashboard's "last 24 hours" always covers the fixture data.

Run it with a command to run against it::

    python -m tests.fixture_api run -- pnpm exec bru run api-tests -r --env local

or on its own, until interrupted, for Playwright and for looking at the dashboard by hand::

    python -m tests.fixture_api serve
"""

import argparse
import asyncio
import logging
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from panel.api.app import create_app
from panel.settings import Settings
from panel.store import Progress, Writer
from tests.api_support import ROWS, T0, populate
from tests.support import ManualClock, call_row

API_PORT = 8099
UNREADY_PORT = 8098
CONTROL_PORT = 8097
HOST = "127.0.0.1"
START_TIMEOUT = 30.0


class Background:
    """A uvicorn server on its own thread, stopped by the harness rather than by a signal."""

    def __init__(self, app: FastAPI, port: int) -> None:
        self.url = f"http://{HOST}:{port}"
        config = uvicorn.Config(
            app, host=HOST, port=port, log_level="warning", loop="uvloop", http="httptools"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name=f"fixture:{port}")

    def start(self) -> None:
        self.thread.start()

    def await_start(self) -> None:
        """Wait until the server is serving, rather than until something answers its port.

        A port already in use makes uvicorn exit its thread, and without this the harness would
        cheerfully run the whole suite against whatever is already listening there.
        """
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self.server.started:
                return
            if not self.thread.is_alive():
                raise RuntimeError(f"{self.url} stopped before it started; is that port taken?")
            time.sleep(0.05)
        raise TimeoutError(f"{self.url} did not start within {START_TIMEOUT} seconds")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def control_app(target: str, database: Path, generation: int) -> FastAPI:
    """Reads the API's event stream, and publishes a call the way the worker would."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    published: list[int] = []

    @app.get("/events-probe")
    async def events_probe() -> dict[str, Any]:
        client = httpx.AsyncClient(base_url=target, timeout=10)
        async with client, client.stream("GET", "/events") as response:
            frame = ""
            async for chunk in response.aiter_text():
                frame += chunk
                if frame.endswith("\n\n"):
                    break
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "firstEvent": frame,
        }

    @app.post("/publish-call")
    async def publish_call() -> dict[str, Any]:
        """Commit one more call, so connected browsers are told the data changed."""
        oid = len(ROWS) + 1 + len(published)
        published.append(oid)
        return await asyncio.to_thread(commit_call, database, generation, oid)

    @app.post("/retract-calls")
    async def retract_calls() -> dict[str, int]:
        """Take back every published call, so a flow starts from the fixture dataset."""
        retracted = await asyncio.to_thread(retract, database, generation, tuple(published))
        return {"retracted": retracted}

    @app.get("/healthz")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app


def commit_call(database: Path, generation: int, oid: int) -> dict[str, Any]:
    row = call_row(
        oid,
        Call_Received=T0 - 60_000,
        ZONE_="15",
        Sector="C1",
        Tencode_Description="SHOTS FIRED",
        Disposition_Description=None,
    )
    writer = Writer(database, clock=ManualClock(T0))
    writer.lock()
    try:
        writer.commit_page(generation, [row], Progress("live", cursor=oid))
    finally:
        writer.close()
    return {"id": f"{generation}:{oid}", "OBJECTID": oid}


def retract(database: Path, generation: int, oids: Iterable[int]) -> int:
    writer = Writer(database, clock=ManualClock(T0))
    writer.lock()
    try:
        return writer.mark_removed(generation, oids)
    finally:
        writer.close()


def fixture_database(directory: Path) -> tuple[Path, int]:
    """The fixture dataset, timestamped by the same fixed clock the API reports as its own."""
    database = directory / "panel.sqlite"
    generation = populate(database, ManualClock(T0))
    return database, generation


def wait_for(url: str) -> None:
    """Wait until a service answers its liveness check."""
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.05)
    raise TimeoutError(f"{url} did not start within {START_TIMEOUT} seconds")


@contextmanager
def fixture_services(directory: Path) -> Generator[Sequence[Background]]:
    """The API, an unready API, and the event-stream probe, all serving and answering."""
    database, generation = fixture_database(directory)
    settings = Settings(database=database)
    # The unready API cannot read a data version from a database with no tables, and says so on
    # every poll. That is the behaviour under test, so the harness keeps it out of the output.
    logging.getLogger("panel.api.events").setLevel(logging.CRITICAL)
    unready = Settings(database=directory / "unmigrated.sqlite", event_poll_seconds=60)
    # The API reports the fixture clock as its own time, so the dashboard builds its windows
    # around the fixture data instead of around whatever day the suite happens to run on.
    api = Background(create_app(settings, clock=ManualClock(T0)), API_PORT)
    services = [
        api,
        Background(create_app(unready), UNREADY_PORT),
        Background(control_app(api.url, database, generation), CONTROL_PORT),
    ]
    for service in services:
        service.start()
    try:
        for service in services:
            service.await_start()
            wait_for(service.url)
        yield services
    finally:
        for service in services:
            service.stop()


def serve(_arguments: argparse.Namespace, directory: Path) -> int:
    with fixture_services(directory) as services:
        for service in services:
            print(f"serving {service.url}")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0


def run(arguments: argparse.Namespace, directory: Path) -> int:
    # argparse.REMAINDER keeps the "--" that separates this command from the one to run.
    command = [word for word in arguments.command if word != "--"]
    with fixture_services(directory):
        return subprocess.run(command, check=False).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="where to put the fixture database")
    subcommands = parser.add_subparsers(dest="command_name", required=True)
    subcommands.add_parser("serve", help="serve until interrupted").set_defaults(run=serve)
    running = subcommands.add_parser("run", help="serve while a command runs")
    running.add_argument("command", nargs=argparse.REMAINDER)
    running.set_defaults(run=run)
    arguments = parser.parse_args(argv)
    if arguments.directory is not None:
        arguments.directory.mkdir(parents=True, exist_ok=True)
        return int(arguments.run(arguments, arguments.directory))
    with tempfile.TemporaryDirectory(prefix="panel-fixture-") as temporary:
        return int(arguments.run(arguments, Path(temporary)))


if __name__ == "__main__":
    sys.exit(main())
