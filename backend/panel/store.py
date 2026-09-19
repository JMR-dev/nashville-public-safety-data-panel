"""Ingestion persistence. The worker process is the only writer."""

import fcntl
import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from io import TextIOWrapper
from pathlib import Path
from typing import Any, TypedDict, Unpack

from sqlalchemy import Connection, RowMapping, select, update
from sqlalchemy.dialects.sqlite import insert

from panel.database import open_engine, upgrade
from panel.tables import (
    ATTRIBUTE_FIELDS,
    calls,
    checkpoints,
    generations,
    schema_snapshots,
    source_status,
    state,
)

Clock = Callable[[], int]
CHUNK = 500


def system_clock() -> int:
    return time.time_ns() // 1_000_000


class SourceState(StrEnum):
    STARTING = "starting"
    BACKFILLING = "backfilling"
    LIVE = "live"
    DEGRADED = "degraded"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"


class WriterBusy(Exception):
    """Another process already holds this database's writer lock."""


@dataclass(frozen=True)
class Provenance:
    url: str
    service_item_id: str | None
    layer_name: str | None


@dataclass(frozen=True)
class Generation:
    id: int
    source: str
    url: str
    service_item_id: str | None
    layer_name: str | None
    reason: str
    created_at: int
    active: bool
    retired_at: int | None
    boundary: int | None
    backfill_completed_at: int | None


@dataclass(frozen=True)
class Progress:
    """A checkpoint advanced in the same transaction as the page it follows."""

    name: str
    cursor: int
    complete: bool = False


@dataclass(frozen=True)
class RangeCheckpoint:
    name: str
    lower: int
    upper: int
    cursor: int
    completed: bool


@dataclass(frozen=True)
class PageResult:
    inserted: int
    changed: int
    unchanged: int
    version: int


class StatusChanges(TypedDict, total=False):
    generation: int | None
    state: SourceState
    detail: str | None
    last_poll_at: int | None
    upstream_edit_at: int | None
    degraded_since: int | None
    retry_at: int | None
    window_reconciled_at: int | None
    full_reconciled_at: int | None


def fingerprint(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def range_name(lower: int, upper: int) -> str:
    return f"backfill:{lower}-{upper}"


def _chunks(values: Sequence[int]) -> Iterable[Sequence[int]]:
    return (values[start : start + CHUNK] for start in range(0, len(values), CHUNK))


def _generation(row: RowMapping) -> Generation:
    return Generation(**row)


class Writer:
    def __init__(self, path: Path, *, clock: Clock = system_clock) -> None:
        self.path = path
        self.clock = clock
        self.engine = open_engine(path, writer=True)
        self._lock: TextIOWrapper | None = None

    def lock(self) -> None:
        handle = self.path.with_name(self.path.name + ".writer-lock").open("w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise WriterBusy(f"{self.path} already has a writer") from error
        self._lock = handle

    def initialize(self) -> None:
        upgrade(self.engine)

    def close(self) -> None:
        self.engine.dispose()
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    # Generations and provenance

    def start_generation(self, source: str, provenance: Provenance, reason: str) -> Generation:
        now = self.clock()
        with self.engine.begin() as connection:
            connection.execute(
                update(generations)
                .where(generations.c.source == source, generations.c.active.is_(True))
                .values(active=False, retired_at=now)
            )
            row = connection.execute(
                insert(generations)
                .values(
                    source=source,
                    url=provenance.url,
                    service_item_id=provenance.service_item_id,
                    layer_name=provenance.layer_name,
                    reason=reason,
                    created_at=now,
                    active=True,
                )
                .returning(*generations.c)
            )
            return _generation(row.mappings().one())

    def active_generation(self, source: str) -> Generation | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(generations).where(
                    generations.c.source == source, generations.c.active.is_(True)
                )
            )
            found = row.mappings().first()
            return None if found is None else _generation(found)

    def generation(self, generation: int) -> Generation:
        with self.engine.connect() as connection:
            row = connection.execute(select(generations).where(generations.c.id == generation))
            return _generation(row.mappings().one())

    # Backfill boundary and checkpoints

    def capture_boundary(
        self, generation: int, boundary: int, ranges: Sequence[tuple[int, int]]
    ) -> None:
        """Record the backfill boundary and its disjoint ranges once per generation."""
        now = self.clock()
        with self.engine.begin() as connection:
            captured = connection.execute(
                update(generations)
                .where(generations.c.id == generation, generations.c.boundary.is_(None))
                .values(boundary=boundary)
            )
            if captured.rowcount == 0:
                return
            for lower, upper in ranges:
                connection.execute(
                    insert(checkpoints).values(
                        generation=generation,
                        name=range_name(lower, upper),
                        lower=lower,
                        upper=upper,
                        cursor=lower,
                        updated_at=now,
                    )
                )

    def ranges(self, generation: int) -> list[RangeCheckpoint]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(checkpoints)
                .where(checkpoints.c.generation == generation, checkpoints.c.lower.is_not(None))
                .order_by(checkpoints.c.lower)
            )
            return [
                RangeCheckpoint(
                    name=row["name"],
                    lower=row["lower"],
                    upper=row["upper"],
                    cursor=row["cursor"],
                    completed=row["completed_at"] is not None,
                )
                for row in rows.mappings()
            ]

    def checkpoint(self, generation: int, name: str) -> int | None:
        with self.engine.connect() as connection:
            return connection.execute(
                select(checkpoints.c.cursor).where(
                    checkpoints.c.generation == generation, checkpoints.c.name == name
                )
            ).scalar()

    def complete_backfill(self, generation: int) -> bool:
        """Mark the boundary complete only once every range has completed."""
        now = self.clock()
        with self.engine.begin() as connection:
            pending = connection.execute(
                select(checkpoints.c.name).where(
                    checkpoints.c.generation == generation,
                    checkpoints.c.lower.is_not(None),
                    checkpoints.c.completed_at.is_(None),
                )
            ).first()
            if pending is not None:
                return False
            boundary = connection.execute(
                update(generations)
                .where(generations.c.id == generation)
                .values(backfill_completed_at=now)
                .returning(generations.c.boundary)
            ).scalar_one()
            connection.execute(
                insert(checkpoints)
                .values(generation=generation, name="live", cursor=boundary, updated_at=now)
                .on_conflict_do_nothing()
            )
            return True

    # Records

    def commit_page(
        self,
        generation: int,
        rows: Sequence[Mapping[str, Any]],
        progress: Progress | None = None,
    ) -> PageResult:
        """Upsert one page of source records and its checkpoint in a single transaction."""
        now = self.clock()
        with self.engine.begin() as connection:
            version = self._version(connection)
            pending = version + 1
            by_id = {int(row["OBJECTID"]): row for row in rows}
            existing = self._existing(connection, generation, list(by_id))
            inserted = changed = 0
            unchanged: list[int] = []
            for oid, row in by_id.items():
                digest = fingerprint(row)
                values: dict[str, Any] = {name: row.get(name) for name in ATTRIBUTE_FIELDS}
                values.update(raw=dict(row), fingerprint=digest, last_seen_at=now)
                previous = existing.get(oid)
                if previous is None:
                    connection.execute(
                        insert(calls).values(
                            generation=generation,
                            OBJECTID=oid,
                            first_seen_at=now,
                            last_changed_at=now,
                            first_seen_version=pending,
                            changed_version=pending,
                            source_present=True,
                            **values,
                        )
                    )
                    inserted += 1
                elif previous != (digest, True):
                    connection.execute(
                        update(calls)
                        .where(calls.c.generation == generation, calls.c.OBJECTID == oid)
                        .values(
                            last_changed_at=now,
                            changed_version=pending,
                            source_present=True,
                            removed_at=None,
                            **values,
                        )
                    )
                    changed += 1
                else:
                    unchanged.append(oid)
            for chunk in _chunks(unchanged):
                connection.execute(
                    update(calls)
                    .where(calls.c.generation == generation, calls.c.OBJECTID.in_(chunk))
                    .values(last_seen_at=now)
                )
            if progress is not None:
                self._advance(connection, generation, progress, now)
            modified = bool(inserted or changed)
            if modified:
                self._set_version(connection, pending)
            self._touch(connection, generation, now, modified=modified)
            return PageResult(inserted, changed, len(unchanged), pending if modified else version)

    def mark_removed(self, generation: int, oids: Iterable[int]) -> int:
        """Retain records that left the source, recording when they were found missing."""
        now = self.clock()
        ids = sorted(set(oids))
        with self.engine.begin() as connection:
            pending = self._version(connection) + 1
            removed = 0
            for chunk in _chunks(ids):
                removed += connection.execute(
                    update(calls)
                    .where(
                        calls.c.generation == generation,
                        calls.c.OBJECTID.in_(chunk),
                        calls.c.source_present.is_(True),
                    )
                    .values(
                        source_present=False,
                        removed_at=now,
                        last_changed_at=now,
                        changed_version=pending,
                    )
                ).rowcount
            if removed:
                self._set_version(connection, pending)
                self._touch(connection, generation, now, modified=True)
            return removed

    def present_ids(self, generation: int, *, upper: int, since: int | None = None) -> set[int]:
        statement = select(calls.c.OBJECTID).where(
            calls.c.generation == generation,
            calls.c.OBJECTID <= upper,
            calls.c.source_present.is_(True),
        )
        if since is not None:
            statement = statement.where(calls.c.Call_Received >= since)
        with self.engine.connect() as connection:
            return set(connection.execute(statement).scalars())

    # Schema snapshots

    def record_schema(
        self,
        generation: int,
        fields: Sequence[Mapping[str, Any]],
        *,
        compatible: bool,
        problems: Sequence[str],
    ) -> bool:
        """Store a schema snapshot when it differs from the latest one."""
        digest = fingerprint(fields)
        latest = self.latest_schema(generation)
        if latest is not None and latest["fingerprint"] == digest:
            return False
        with self.engine.begin() as connection:
            connection.execute(
                insert(schema_snapshots).values(
                    generation=generation,
                    captured_at=self.clock(),
                    fingerprint=digest,
                    fields=list(fields),
                    compatible=compatible,
                    problems=list(problems),
                )
            )
        return True

    def latest_schema(self, generation: int) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(schema_snapshots)
                .where(schema_snapshots.c.generation == generation)
                .order_by(schema_snapshots.c.id.desc())
                .limit(1)
            ).mappings()
            found = row.first()
            return None if found is None else dict(found)

    # Status and versioning

    def set_status(self, source: str, **changes: Unpack[StatusChanges]) -> None:
        now = self.clock()
        with self.engine.begin() as connection:
            connection.execute(
                insert(source_status)
                .values(
                    {"source": source, "state": SourceState.STARTING, **changes, "updated_at": now}
                )
                .on_conflict_do_update(
                    index_elements=[source_status.c.source],
                    set_={**changes, "updated_at": now},
                )
            )

    def data_version(self) -> int:
        with self.engine.connect() as connection:
            return self._version(connection)

    def backup(self, target: Path) -> None:
        """Copy a consistent snapshot with SQLite's online backup, then publish it atomically."""
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        with (
            closing(sqlite3.connect(self.path)) as source,
            closing(sqlite3.connect(partial)) as destination,
        ):
            source.backup(destination)
        os.replace(partial, target)

    # Transaction helpers

    @staticmethod
    def _version(connection: Connection) -> int:
        value = connection.execute(
            select(state.c.value).where(state.c.key == "data_version")
        ).scalar()
        return 0 if value is None else int(value)

    @staticmethod
    def _set_version(connection: Connection, version: int) -> None:
        connection.execute(
            insert(state)
            .values(key="data_version", value=version)
            .on_conflict_do_update(index_elements=[state.c.key], set_={"value": version})
        )

    @staticmethod
    def _existing(
        connection: Connection, generation: int, oids: Sequence[int]
    ) -> dict[int, tuple[str, bool]]:
        found: dict[int, tuple[str, bool]] = {}
        for chunk in _chunks(oids):
            rows = connection.execute(
                select(calls.c.OBJECTID, calls.c.fingerprint, calls.c.source_present).where(
                    calls.c.generation == generation, calls.c.OBJECTID.in_(chunk)
                )
            )
            found.update({oid: (digest, present) for oid, digest, present in rows.tuples()})
        return found

    @staticmethod
    def _advance(connection: Connection, generation: int, progress: Progress, now: int) -> None:
        completed = now if progress.complete else None
        connection.execute(
            insert(checkpoints)
            .values(
                generation=generation,
                name=progress.name,
                cursor=progress.cursor,
                completed_at=completed,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[checkpoints.c.generation, checkpoints.c.name],
                set_={"cursor": progress.cursor, "completed_at": completed, "updated_at": now},
            )
        )

    @staticmethod
    def _touch(connection: Connection, generation: int, now: int, *, modified: bool) -> None:
        source = connection.execute(
            select(generations.c.source).where(generations.c.id == generation)
        ).scalar_one()
        changes: dict[str, int] = {"last_poll_at": now, "updated_at": now}
        if modified:
            changes["last_change_at"] = now
        connection.execute(
            insert(source_status)
            .values(source=source, state=SourceState.STARTING, **changes)
            .on_conflict_do_update(index_elements=[source_status.c.source], set_=changes)
        )
