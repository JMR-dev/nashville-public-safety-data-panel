"""SQLite persistence. All ingestion mutations happen through one writer."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, and_, create_engine, event, func, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from panel.tables import (
    INTEGER_FIELDS, REAL_FIELDS, TEXT_FIELDS, calls, checkpoints, generations, state,
)


def configure_sqlite(connection: sqlite3.Connection, _record: Any) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.engine: Engine = create_engine(f"sqlite:///{path}")
        event.listen(self.engine, "connect", configure_sqlite)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self) -> None:
        self.engine.dispose()

    def get(self, key: str, default: Any = None) -> Any:
        with self.engine.connect() as connection:
            value = connection.execute(select(state.c.value).where(state.c.key == key)).scalar()
            return default if value is None else value

    @staticmethod
    def _put(connection: Connection, key: str, value: Any) -> None:
        connection.execute(insert(state).values(key=key, value=value).on_conflict_do_update(
            index_elements=[state.c.key], set_={"value": value},
        ))

    def put(self, key: str, value: Any) -> None:
        with self.engine.begin() as connection:
            self._put(connection, key, value)

    def version(self) -> int:
        return int(self.get("version", 0))

    def generation(self, source: str, upper: int, *, reset: bool = False) -> int:
        with self.engine.begin() as connection:
            row = connection.execute(select(generations).where(
                generations.c.source == source, generations.c.active.is_(True),
            )).mappings().first()
            if row is not None and not reset:
                return int(row["id"])
            connection.execute(generations.update().where(generations.c.source == source)
                               .values(active=False))
            result = connection.execute(generations.insert().values(
                source=source, upper=upper, active=True, schema={},
            ).returning(generations.c.id))
            return int(result.scalar_one())

    def generation_info(self, generation: int) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return dict(connection.execute(select(generations).where(
                generations.c.id == generation,
            )).mappings().one())

    def save_schema(self, generation: int, schema: dict[str, str]) -> None:
        with self.engine.begin() as connection:
            connection.execute(generations.update().where(generations.c.id == generation)
                               .values(schema=schema))

    def checkpoint(self, generation: int, name: str) -> int:
        with self.engine.connect() as connection:
            value = connection.execute(select(checkpoints.c.cursor).where(
                checkpoints.c.generation == generation, checkpoints.c.name == name,
            )).scalar()
            return 0 if value is None else int(value)

    def commit_page(
        self, generation: int, name: str, rows: list[dict[str, Any]], cursor: int, run: str = "",
    ) -> None:
        now = int(time.time() * 1000)
        with self.engine.begin() as connection:
            changed = False
            for row in rows:
                fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                existing = connection.execute(select(calls.c.fingerprint, calls.c.source_present)
                    .where(calls.c.generation == generation, calls.c.OBJECTID == row["OBJECTID"]))
                old = existing.first()
                values: dict[str, Any] = {
                    field: row.get(field) for field in (*TEXT_FIELDS, *REAL_FIELDS, *INTEGER_FIELDS)
                }
                values.update(raw=row, fingerprint=fingerprint, observed_at=now,
                              source_present=True, seen_run=run)
                connection.execute(insert(calls).values(
                    generation=generation, OBJECTID=row["OBJECTID"], **values,
                ).on_conflict_do_update(index_elements=[calls.c.generation, calls.c.OBJECTID],
                                        set_=values))
                changed = changed or old is None or old.fingerprint != fingerprint or not old.source_present
            connection.execute(insert(checkpoints).values(
                generation=generation, name=name, cursor=cursor,
            ).on_conflict_do_update(index_elements=[checkpoints.c.generation, checkpoints.c.name],
                                    set_={"cursor": cursor}))
            self._put(connection, "last_poll", now)
            if changed:
                self._bump(connection, now)

    def _bump(self, connection: Connection, now: int) -> None:
        version = connection.execute(select(state.c.value).where(state.c.key == "version")).scalar()
        self._put(connection, "version", int(version or 0) + 1)
        self._put(connection, "last_change", now)

    def mark_missing(self, generation: int, run: str, upper: int) -> None:
        with self.engine.begin() as connection:
            ids = connection.execute(select(calls.c.OBJECTID).where(
                calls.c.generation == generation, calls.c.OBJECTID <= upper,
                calls.c.seen_run != run, calls.c.source_present.is_(True),
            )).scalars().all()
            if ids:
                connection.execute(calls.update().where(
                    calls.c.generation == generation, calls.c.OBJECTID.in_(ids),
                ).values(source_present=False))
                self._bump(connection, int(time.time() * 1000))

    def query(
        self, filters: dict[str, Any], limit: int, cursor: tuple[int, int, int] | None,
    ) -> list[dict[str, Any]]:
        statement = select(calls)
        for key, value in filters.items():
            if key == "since":
                statement = statement.where(calls.c.Call_Received >= value)
            elif key == "until":
                statement = statement.where(calls.c.Call_Received <= value)
            else:
                statement = statement.where(calls.c[key] == value)
        timestamp = func.coalesce(calls.c.Call_Received, 0)
        if cursor is not None:
            received, oid, generation = cursor
            statement = statement.where(or_(timestamp < received,
                and_(timestamp == received, calls.c.OBJECTID < oid),
                and_(timestamp == received, calls.c.OBJECTID == oid,
                     calls.c.generation < generation)))
        statement = statement.order_by(timestamp.desc(), calls.c.OBJECTID.desc(),
                                       calls.c.generation.desc()).limit(limit)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    def detail(self, generation: int, oid: int) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(calls).where(
                calls.c.generation == generation, calls.c.OBJECTID == oid,
            )).mappings().first()
            return None if row is None else dict(row)

    def facets(self) -> dict[str, list[str]]:
        with self.engine.connect() as connection:
            return {field: list(connection.execute(select(calls.c[field]).where(
                calls.c[field].is_not(None),
            ).distinct().order_by(calls.c[field])).scalars()) for field in (
                "ZONE_", "Sector", "Tencode_Description", "Disposition_Description",
            )}

    def backup(self, destination: Path) -> None:
        with sqlite3.connect(self.path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
