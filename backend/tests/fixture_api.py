"""A fixture-backed API to run the Bruno collections and the Playwright flows against.

The records are the ones the API's own tests use, so every suite asserts against one dataset.
Three services are served, each on its own port:

* the API, on a populated database;
* the same API on a database that was never migrated, which is what an unready deployment
  looks like to ``/readyz``;
* a probe that reads the API's event stream over HTTP and reports the response it got. The
  stream never ends, and the Bruno CLI cannot read a response that never ends, so this is how
  a collection asserts the stream's HTTP contract.

Run it with a command to run against it::

    python -m tests.fixture_api run -- pnpm exec bru run api-tests -r --env local

or on its own, until interrupted, for Playwright and for looking at the dashboard by hand::

    python -m tests.fixture_api serve
"""

import argparse
import logging
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from panel.api.app import create_app
from panel.settings import Settings
from tests.api_support import populate
from tests.support import ManualClock

API_PORT = 8099
UNREADY_PORT = 8098
PROBE_PORT = 8097
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

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def probe_app(target: str) -> FastAPI:
    """Reports what the API's event stream answered, including the first event it sent."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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

    @app.get("/healthz")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app


def fixture_database(directory: Path) -> Path:
    database = directory / "panel.sqlite"
    populate(database, ManualClock())
    return database


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
    settings = Settings(database=fixture_database(directory))
    # The unready API cannot read a data version from a database with no tables, and says so on
    # every poll. That is the behaviour under test, so the harness keeps it out of the output.
    logging.getLogger("panel.api.events").setLevel(logging.CRITICAL)
    unready = Settings(database=directory / "unmigrated.sqlite", event_poll_seconds=60)
    api = Background(create_app(settings), API_PORT)
    services = [
        api,
        Background(create_app(unready), UNREADY_PORT),
        Background(probe_app(api.url), PROBE_PORT),
    ]
    for service in services:
        service.start()
    try:
        for service in services:
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
