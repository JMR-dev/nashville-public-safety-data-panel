"""SQLite engines and schema migrations."""

import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, event

MIGRATIONS = Path(__file__).parent / "migrations"
BUSY_TIMEOUT_MS = 5000


def open_engine(path: Path, *, writer: bool = False) -> Engine:
    """Open a pooled engine for one SQLite file.

    Transactions are started explicitly so reads see one consistent WAL snapshot and the
    writer takes SQLite's write lock up front (``BEGIN IMMEDIATE``) instead of failing when it
    upgrades a read lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    begin = "BEGIN IMMEDIATE" if writer else "BEGIN"

    def configure(connection: sqlite3.Connection, _record: Any) -> None:
        connection.isolation_level = None
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys=ON")

    def start(connection: Connection) -> None:
        connection.exec_driver_sql(begin)

    event.listen(engine, "connect", configure)
    event.listen(engine, "begin", start)
    return engine


def _config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["connection"] = connection
    return config


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as connection:
        command.upgrade(_config(connection), revision)


def downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.downgrade(_config(connection), revision)


def head_revision() -> str | None:
    return ScriptDirectory(str(MIGRATIONS)).get_current_head()


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()
