"""The ``panel`` command: migrate, ingest, serve, export the contract, and back up.

The worker owns the database: it runs the migrations and holds the writer lock. The API only
reads, so it can be started at any time; its readiness endpoint reports whether the schema is
present yet.
"""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

import httpx
import uvicorn

from panel.api.app import create_app
from panel.api.schema import build_schema
from panel.backup import take_backup
from panel.database import head_revision
from panel.settings import Settings
from panel.store import Writer, WriterBusy, system_clock
from panel.worker import Worker

log = logging.getLogger("panel")


def migrate(_arguments: argparse.Namespace, settings: Settings) -> int:
    writer = Writer(settings.database)
    writer.lock()
    writer.initialize()
    writer.close()
    print(f"{settings.database} is at revision {head_revision()}")
    return 0


def worker(_arguments: argparse.Namespace, settings: Settings) -> int:
    asyncio.run(ingest(settings))
    return 0


async def ingest(settings: Settings) -> None:
    """Ingest until the service is asked to stop."""
    client = httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout_seconds,
    )
    running = Worker(settings, Writer(settings.database), client)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stop.set)
    try:
        await running.run(stop)
    finally:
        await running.close()


def api(arguments: argparse.Namespace, settings: Settings) -> int:
    uvicorn.run(
        create_app(settings),
        host=arguments.host,
        port=arguments.port,
        loop="uvloop",
        http="httptools",
        ws="none",
        log_level="info",
    )
    return 0


def schema(_arguments: argparse.Namespace, settings: Settings) -> int:
    print(build_schema(settings))
    return 0


def backup(_arguments: argparse.Namespace, settings: Settings) -> int:
    if not settings.database.exists():
        print(f"No database at {settings.database}", file=sys.stderr)
        return 1
    target = take_backup(
        settings.database, settings.backup_dir, settings.backup_keep, system_clock()
    )
    print(f"Backed up {settings.database} to {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="panel", description=__doc__)
    commands = parser.add_subparsers(required=True)

    migrating = commands.add_parser("migrate", help="Create or update the database schema")
    migrating.set_defaults(run=migrate)

    ingesting = commands.add_parser("worker", help="Collect calls from the source")
    ingesting.set_defaults(run=worker)

    serving = commands.add_parser("api", help="Serve the read-only API")
    serving.add_argument("--host", default="127.0.0.1")
    serving.add_argument("--port", type=int, default=8000)
    serving.set_defaults(run=api)

    exporting = commands.add_parser("schema", help="Print the GraphQL schema")
    exporting.set_defaults(run=schema)

    copying = commands.add_parser("backup", help="Copy the database and prune old copies")
    copying.set_defaults(run=backup)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exit_code:
        return int(exit_code.code or 0)
    settings = Settings()
    try:
        result: int = arguments.run(arguments, settings)
    except WriterBusy as busy:
        print(str(busy), file=sys.stderr)
        return 1
    return result
