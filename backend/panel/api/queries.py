"""Read queries for the public API. Each runs inside one request's snapshot transaction."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    and_,
    case,
    func,
    literal,
    not_,
    or_,
    select,
    tuple_,
)

from panel.tables import calls, checkpoints, generations, source_status, state

HOUR_MS = 3_600_000
FACET_COLUMNS = ("ZONE_", "Sector", "Tencode_Description", "Disposition_Description")

# A usable location: both coordinates published, in range, and not the 0,0 placeholder.
HAS_COORDINATES: ColumnElement[bool] = and_(
    calls.c.Latitude.between(-90, 90),
    calls.c.Longitude.between(-180, 180),
    not_(and_(calls.c.Latitude == 0, calls.c.Longitude == 0)),
)

Cursor = tuple[int, int, int]


@dataclass(frozen=True)
class CallFilter:
    """Epoch-millisecond bounds (``since`` inclusive, ``until`` exclusive) and upstream values."""

    since: int
    until: int
    ZONE_: tuple[str, ...] = ()
    Sector: tuple[str, ...] = ()
    Tencode_Description: tuple[str, ...] = ()
    Disposition_Description: tuple[str, ...] = ()
    include_removed: bool = False

    def values(self, column: str) -> tuple[str, ...]:
        match column:
            case "ZONE_":
                return self.ZONE_
            case "Sector":
                return self.Sector
            case "Tencode_Description":
                return self.Tencode_Description
            case _:
                return self.Disposition_Description


def _conditions(where: CallFilter, as_of: int | None = None) -> list[ColumnElement[bool]]:
    """The filter's meaning, shared by the feed, map, summaries, and arrival counts.

    With ``as_of``, membership is pinned to that data version: later arrivals are excluded and
    later removals still count as present, so a reader's list does not shift until refreshed.
    """
    conditions: list[ColumnElement[bool]] = [
        calls.c.Call_Received >= where.since,
        calls.c.Call_Received < where.until,
    ]
    for column in FACET_COLUMNS:
        if selected := where.values(column):
            conditions.append(calls.c[column].in_(selected))
    if as_of is not None:
        conditions.append(calls.c.first_seen_version <= as_of)
    if not where.include_removed:
        present = calls.c.source_present.is_(True)
        conditions.append(
            present if as_of is None else or_(present, calls.c.changed_version > as_of)
        )
    return conditions


def _rows(connection: Connection, statement: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(statement).mappings()]


def data_version(connection: Connection) -> int:
    value = connection.execute(select(state.c.value).where(state.c.key == "data_version")).scalar()
    return 0 if value is None else int(value)


def call_page(
    connection: Connection, where: CallFilter, *, first: int, after: Cursor | None, as_of: int
) -> tuple[list[dict[str, Any]], bool]:
    """Newest-first calls pinned to ``as_of``, and whether more follow."""
    statement = select(calls, HAS_COORDINATES.label("has_coordinates")).where(
        *_conditions(where, as_of)
    )
    if after is not None:
        position = tuple_(calls.c.Call_Received, calls.c.OBJECTID, calls.c.generation)
        statement = statement.where(position < tuple_(*(literal(part) for part in after)))
    statement = statement.order_by(
        calls.c.Call_Received.desc(), calls.c.OBJECTID.desc(), calls.c.generation.desc()
    ).limit(first + 1)
    rows = _rows(connection, statement)
    return rows[:first], len(rows) > first


def new_call_count(connection: Connection, where: CallFilter, since_version: int) -> int:
    statement = (
        select(func.count())
        .select_from(calls)
        .where(*_conditions(where), calls.c.first_seen_version > since_version)
    )
    return connection.execute(statement).scalar_one()


def call(connection: Connection, generation: int, oid: int) -> dict[str, Any] | None:
    statement = select(calls, HAS_COORDINATES.label("has_coordinates")).where(
        calls.c.generation == generation, calls.c.OBJECTID == oid
    )
    row = connection.execute(statement).mappings().first()
    return None if row is None else dict(row)


def counts(connection: Connection, where: CallFilter) -> tuple[int, int]:
    """Matching calls, and how many of them have usable coordinates."""
    statement = select(
        func.count(), func.coalesce(func.sum(case((HAS_COORDINATES, 1), else_=0)), 0)
    ).where(*_conditions(where))
    total, located = connection.execute(statement).one()
    return int(total), int(located)


def map_calls(connection: Connection, where: CallFilter, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(calls, HAS_COORDINATES.label("has_coordinates"))
        .where(*_conditions(where), HAS_COORDINATES)
        .order_by(calls.c.Call_Received.desc(), calls.c.OBJECTID.desc())
        .limit(limit)
    )
    return _rows(connection, statement)


def type_counts(
    connection: Connection, where: CallFilter, limit: int
) -> tuple[list[tuple[str | None, int]], int]:
    """The most frequent call types, and how many calls fall outside them."""
    count = func.count().label("count")
    statement = (
        select(calls.c.Tencode_Description, count)
        .where(*_conditions(where))
        .group_by(calls.c.Tencode_Description)
        .order_by(count.desc(), calls.c.Tencode_Description)
    )
    entries = [(row[0], int(row[1])) for row in connection.execute(statement).tuples()]
    return entries[:limit], sum(total for _, total in entries[limit:])


def hourly(connection: Connection, where: CallFilter) -> list[tuple[int, int]]:
    """Calls per UTC hour; Chicago's offsets are whole hours, so buckets align locally too."""
    bucket = (calls.c.Call_Received // HOUR_MS * HOUR_MS).label("bucket")
    statement = (
        select(bucket, func.count()).where(*_conditions(where)).group_by(bucket).order_by(bucket)
    )
    return [(int(start), int(total)) for start, total in connection.execute(statement).tuples()]


def filter_values(connection: Connection, since: int, until: int) -> dict[str, list[str]]:
    scope = CallFilter(since=since, until=until)
    values: dict[str, list[str]] = {}
    for name in FACET_COLUMNS:
        column = calls.c[name]
        statement = (
            select(column)
            .where(*_conditions(scope), column.is_not(None))
            .distinct()
            .order_by(column)
        )
        values[name] = list(connection.execute(statement).scalars())
    return values


def statuses(connection: Connection) -> list[dict[str, Any]]:
    """Per-source status with provenance, backfill progress, and the newest call received."""
    result: list[dict[str, Any]] = []
    for row in _rows(connection, select(source_status).order_by(source_status.c.source)):
        generation = row["generation"]
        row["generation_info"] = (
            None
            if generation is None
            else connection.execute(select(generations).where(generations.c.id == generation))
            .mappings()
            .one()
        )
        row["ranges"] = _rows(
            connection,
            select(
                checkpoints.c.lower,
                checkpoints.c.upper,
                checkpoints.c.cursor,
                checkpoints.c.completed_at,
            )
            .where(checkpoints.c.generation == generation, checkpoints.c.lower.is_not(None))
            .order_by(checkpoints.c.lower),
        )
        row["latest_call_received"] = connection.execute(
            select(func.max(calls.c.Call_Received)).where(
                calls.c.generation == generation, calls.c.source_present.is_(True)
            )
        ).scalar()
        result.append(row)
    return result


def backfill_fraction(ranges: Sequence[dict[str, Any]]) -> float:
    span = sum(entry["upper"] - entry["lower"] for entry in ranges)
    covered = sum(entry["cursor"] - entry["lower"] for entry in ranges)
    return 1.0 if span == 0 else covered / span
