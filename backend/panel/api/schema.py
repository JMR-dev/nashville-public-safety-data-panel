"""The public, read-only GraphQL schema.

Source record fields keep their upstream names and types under ``Call.record``. Everything
else is derived or local metadata and uses GraphQL naming. Times are UTC.
"""

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, NewType

import strawberry
from graphql import GraphQLError
from strawberry.extensions import MaskErrors, MaxAliasesLimiter, MaxTokensLimiter
from strawberry.fastapi import BaseContext
from strawberry.scalars import JSON
from strawberry.schema.config import StrawberryConfig

from panel.api import queries
from panel.api.limits import DocumentLimits, Limits, Size
from panel.api.snapshot import Snapshot
from panel.payload import as_list
from panel.settings import Settings
from panel.store import Clock, SourceState
from panel.tables import SOURCE_FIELDS

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_TYPE_ENTRIES = 100

EpochMilliseconds = NewType("EpochMilliseconds", int)

strawberry.enum(SourceState, name="SourceState")


class InputError(Exception):
    """A request the client can correct; its message is safe to return."""


class Context(BaseContext):
    def __init__(self, snapshot: Snapshot, settings: Settings, clock: Clock) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.settings = settings
        self.clock = clock


Info = strawberry.Info[Context, None]


def utc(epoch_ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=epoch_ms)


def maybe_utc(epoch_ms: int | None) -> datetime | None:
    return None if epoch_ms is None else utc(epoch_ms)


def epoch_ms(value: datetime, name: str) -> int:
    if value.utcoffset() is None:
        raise InputError(f"{name} must include a time zone")
    return (value - EPOCH) // timedelta(milliseconds=1)


def check_range(value: int, name: str, upper: int) -> None:
    if not 1 <= value <= upper:
        raise InputError(f"{name} must be between 1 and {upper}")


def check_version(value: int | None, name: str) -> None:
    if value is not None and value < 0:
        raise InputError(f"{name} must not be negative")


def date_range(since: datetime, until: datetime, settings: Settings) -> tuple[int, int]:
    start, end = epoch_ms(since, "since"), epoch_ms(until, "until")
    if start >= end:
        raise InputError("since must be earlier than until")
    if end - start > settings.max_range_days * 86_400_000:
        raise InputError(f"The date range cannot exceed {settings.max_range_days} days")
    return start, end


def encode_cursor(row: Mapping[str, Any]) -> str:
    position = [row["Call_Received"], row["OBJECTID"], row["generation"]]
    return base64.urlsafe_b64encode(json.dumps(position).encode()).decode()


def decode_cursor(cursor: str) -> queries.Cursor:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError as error:
        raise InputError("Invalid cursor") from error
    position = as_list(decoded) or []
    if len(position) != 3 or any(type(part) is not int for part in position):
        raise InputError("Invalid cursor")
    return position[0], position[1], position[2]


@strawberry.input(description="Calls received in [since, until), narrowed by upstream values.")
class CallFilter:
    since: datetime
    until: datetime
    ZONE_: list[str] = strawberry.field(default_factory=list, name="ZONE_")
    Sector: list[str] = strawberry.field(default_factory=list, name="Sector")
    Tencode_Description: list[str] = strawberry.field(
        default_factory=list, name="Tencode_Description"
    )
    Disposition_Description: list[str] = strawberry.field(
        default_factory=list, name="Disposition_Description"
    )
    include_removed: bool = strawberry.field(
        default=False, description="Include records that are no longer in the source."
    )

    def resolve(self, settings: Settings) -> queries.CallFilter:
        since, until = date_range(self.since, self.until, settings)
        return queries.CallFilter(
            since=since,
            until=until,
            ZONE_=tuple(self.ZONE_),
            Sector=tuple(self.Sector),
            Tencode_Description=tuple(self.Tencode_Description),
            Disposition_Description=tuple(self.Disposition_Description),
            include_removed=self.include_removed,
        )


@strawberry.type(description="A published call record with its upstream field names and types.")
class MnpdCallRecord:
    OBJECTID: int = strawberry.field(name="OBJECTID")
    Event_Number: str | None = strawberry.field(name="Event_Number")
    Complaint_Number: float | None = strawberry.field(name="Complaint_Number")
    Tencode: int | None = strawberry.field(name="Tencode")
    Tencode_Description: str | None = strawberry.field(name="Tencode_Description")
    Tencode_Suffix: str | None = strawberry.field(name="Tencode_Suffix")
    Tencode_Suffix_Description: str | None = strawberry.field(name="Tencode_Suffix_Description")
    Disposition_Code: str | None = strawberry.field(name="Disposition_Code")
    Disposition_Description: str | None = strawberry.field(name="Disposition_Description")
    Block: str | None = strawberry.field(name="Block")
    Street_Name: str | None = strawberry.field(name="Street_Name")
    Unit_Dispatched: str | None = strawberry.field(name="Unit_Dispatched")
    Shift: str | None = strawberry.field(name="Shift")
    Sector: str | None = strawberry.field(name="Sector")
    Mapped_Location: str | None = strawberry.field(name="Mapped_Location")
    POINT_X: float | None = strawberry.field(name="POINT_X")
    POINT_Y: float | None = strawberry.field(name="POINT_Y")
    ZONE_: str | None = strawberry.field(name="ZONE_")
    Latitude: float | None = strawberry.field(name="Latitude")
    Longitude: float | None = strawberry.field(name="Longitude")
    RPA: str | None = strawberry.field(name="RPA")
    Call_Received: EpochMilliseconds | None = strawberry.field(name="Call_Received")


@strawberry.type(description="A retained call record with derived and local metadata.")
class Call:
    id: strawberry.ID = strawberry.field(description="Source generation and OBJECTID.")
    generation: int
    received_at: datetime | None = strawberry.field(description="Call_Received in UTC.")
    has_coordinates: bool = strawberry.field(
        description="Whether the source published a usable approximate location."
    )
    source_present: bool = strawberry.field(
        description="Whether the record was in the source at its last reconciliation."
    )
    removed_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime
    additional_attributes: JSON = strawberry.field(
        description="Published attributes that are not part of the known schema."
    )
    record: MnpdCallRecord


def build_call(row: Mapping[str, Any]) -> Call:
    return Call(
        id=strawberry.ID(f"{row['generation']}:{row['OBJECTID']}"),
        generation=row["generation"],
        received_at=maybe_utc(row["Call_Received"]),
        has_coordinates=bool(row["has_coordinates"]),
        source_present=row["source_present"],
        removed_at=maybe_utc(row["removed_at"]),
        first_seen_at=utc(row["first_seen_at"]),
        last_seen_at=utc(row["last_seen_at"]),
        last_changed_at=utc(row["last_changed_at"]),
        additional_attributes=JSON(row["extra"]),
        record=MnpdCallRecord(**{name: row[name] for name in SOURCE_FIELDS}),
    )


@strawberry.type
class PageInfo:
    end_cursor: str | None
    has_next_page: bool


@strawberry.type(description="One page of the newest-first feed, pinned to a data version.")
class CallPage:
    nodes: list[Call]
    page_info: PageInfo
    as_of_version: int


@strawberry.type(description="Map markers are bounded; counts cover every matching call.")
class MapCalls:
    calls: list[Call]
    matching: int
    with_coordinates: int
    truncated: bool


@strawberry.type
class TypeCount:
    Tencode_Description: str | None = strawberry.field(name="Tencode_Description")
    count: int


@strawberry.type
class TypeBreakdown:
    entries: list[TypeCount]
    other_count: int = strawberry.field(description="Calls whose type is not listed.")


@strawberry.type
class HourCount:
    start: datetime
    count: int


@strawberry.type(description="Counts of published source records, not confirmed incidents.")
class Summary:
    total: int
    with_coordinates: int
    without_coordinates: int
    where: strawberry.Private[queries.CallFilter]

    @strawberry.field
    async def types(self, info: Info, limit: int = 10) -> TypeBreakdown:
        check_range(limit, "limit", MAX_TYPE_ENTRIES)
        entries, other = await info.context.snapshot.run(
            lambda connection: queries.type_counts(connection, self.where, limit)
        )
        return TypeBreakdown(
            entries=[TypeCount(Tencode_Description=name, count=count) for name, count in entries],
            other_count=other,
        )

    @strawberry.field
    async def hourly(self, info: Info) -> list[HourCount]:
        buckets = await info.context.snapshot.run(
            lambda connection: queries.hourly(connection, self.where)
        )
        return [HourCount(start=utc(start), count=count) for start, count in buckets]


@strawberry.type
class FilterValues:
    ZONE_: list[str] = strawberry.field(name="ZONE_")
    Sector: list[str] = strawberry.field(name="Sector")
    Tencode_Description: list[str] = strawberry.field(name="Tencode_Description")
    Disposition_Description: list[str] = strawberry.field(name="Disposition_Description")


@strawberry.type(description="Where the retained data came from.")
class SourceGeneration:
    id: int
    url: str
    service_item_id: str | None
    layer_name: str | None
    reason: str
    created_at: datetime
    active: bool


@strawberry.type
class BackfillProgress:
    boundary: int = strawberry.field(description="Highest OBJECTID captured for the backfill.")
    ranges_total: int
    ranges_completed: int
    fraction: float
    completed_at: datetime | None


@strawberry.type(
    description=(
        "Ingestion health. A successful poll does not mean the source published new records; "
        "compare lastPollAt, lastChangeAt, latestCallReceivedAt, and upstreamEditedAt."
    )
)
class SourceStatus:
    source: str
    state: SourceState
    detail: str | None
    last_poll_at: datetime | None
    last_change_at: datetime | None
    latest_call_received_at: datetime | None
    upstream_edited_at: datetime | None
    degraded_since: datetime | None
    retry_at: datetime | None
    window_reconciled_at: datetime | None
    full_reconciled_at: datetime | None
    generation: SourceGeneration | None
    backfill: BackfillProgress | None


def build_status(row: Mapping[str, Any]) -> SourceStatus:
    info = row["generation_info"]
    generation = None
    backfill = None
    if info is not None:
        generation = SourceGeneration(
            id=info["id"],
            url=info["url"],
            service_item_id=info["service_item_id"],
            layer_name=info["layer_name"],
            reason=info["reason"],
            created_at=utc(info["created_at"]),
            active=info["active"],
        )
        if info["boundary"] is not None:
            ranges = row["ranges"]
            backfill = BackfillProgress(
                boundary=info["boundary"],
                ranges_total=len(ranges),
                ranges_completed=sum(entry["completed_at"] is not None for entry in ranges),
                fraction=queries.backfill_fraction(ranges),
                completed_at=maybe_utc(info["backfill_completed_at"]),
            )
    return SourceStatus(
        source=row["source"],
        state=SourceState(row["state"]),
        detail=row["detail"],
        last_poll_at=maybe_utc(row["last_poll_at"]),
        last_change_at=maybe_utc(row["last_change_at"]),
        latest_call_received_at=maybe_utc(row["latest_call_received"]),
        upstream_edited_at=maybe_utc(row["upstream_edit_at"]),
        degraded_since=maybe_utc(row["degraded_since"]),
        retry_at=maybe_utc(row["retry_at"]),
        window_reconciled_at=maybe_utc(row["window_reconciled_at"]),
        full_reconciled_at=maybe_utc(row["full_reconciled_at"]),
        generation=generation,
        backfill=backfill,
    )


def parse_call_id(value: str) -> tuple[int, int]:
    generation, _, oid = value.partition(":")
    if not (generation.isdecimal() and oid.isdecimal()):
        raise InputError("Invalid call id")
    return int(generation), int(oid)


@strawberry.type
class Query:
    @strawberry.field(description="Newest-first calls. Omit asOfVersion to pin the current one.")
    async def calls(
        self,
        info: Info,
        filter: CallFilter,
        first: int = 50,
        after: str | None = None,
        as_of_version: int | None = None,
    ) -> CallPage:
        settings = info.context.settings
        where = filter.resolve(settings)
        check_range(first, "first", settings.max_page_size)
        check_version(as_of_version, "asOfVersion")
        position = None if after is None else decode_cursor(after)

        def read(connection: Any) -> tuple[list[dict[str, Any]], bool, int]:
            version = queries.data_version(connection) if as_of_version is None else as_of_version
            rows, more = queries.call_page(
                connection, where, first=first, after=position, as_of=version
            )
            return rows, more, version

        rows, more, version = await info.context.snapshot.run(read)
        return CallPage(
            nodes=[build_call(row) for row in rows],
            page_info=PageInfo(
                end_cursor=encode_cursor(rows[-1]) if rows else None, has_next_page=more
            ),
            as_of_version=version,
        )

    @strawberry.field(description="Matching calls first seen after a data version.")
    async def new_call_count(self, info: Info, filter: CallFilter, since_version: int) -> int:
        where = filter.resolve(info.context.settings)
        check_version(since_version, "sinceVersion")
        return await info.context.snapshot.run(
            lambda connection: queries.new_call_count(connection, where, since_version)
        )

    @strawberry.field
    async def call(self, info: Info, id: strawberry.ID) -> Call | None:
        generation, oid = parse_call_id(id)
        row = await info.context.snapshot.run(
            lambda connection: queries.call(connection, generation, oid)
        )
        return None if row is None else build_call(row)

    @strawberry.field(description="Newest matching calls with usable coordinates.")
    async def map_calls(self, info: Info, filter: CallFilter, limit: int = 2000) -> MapCalls:
        settings = info.context.settings
        where = filter.resolve(settings)
        check_range(limit, "limit", settings.max_map_results)

        def read(connection: Any) -> tuple[list[dict[str, Any]], int, int]:
            matching, located = queries.counts(connection, where)
            return queries.map_calls(connection, where, limit), matching, located

        rows, matching, located = await info.context.snapshot.run(read)
        return MapCalls(
            calls=[build_call(row) for row in rows],
            matching=matching,
            with_coordinates=located,
            truncated=located > len(rows),
        )

    @strawberry.field
    async def summary(self, info: Info, filter: CallFilter) -> Summary:
        where = filter.resolve(info.context.settings)
        total, located = await info.context.snapshot.run(
            lambda connection: queries.counts(connection, where)
        )
        return Summary(
            total=total, with_coordinates=located, without_coordinates=total - located, where=where
        )

    @strawberry.field
    async def filter_values(self, info: Info, since: datetime, until: datetime) -> FilterValues:
        start, end = date_range(since, until, info.context.settings)
        values = await info.context.snapshot.run(
            lambda connection: queries.filter_values(connection, start, end)
        )
        return FilterValues(**values)

    @strawberry.field
    async def source_status(self, info: Info) -> list[SourceStatus]:
        rows = await info.context.snapshot.run(queries.statuses)
        return [build_status(row) for row in rows]

    @strawberry.field(description="Increases whenever committed call data changes.")
    async def data_version(self, info: Info) -> int:
        return await info.context.snapshot.run(queries.data_version)

    @strawberry.field
    def server_time(self, info: Info) -> datetime:
        return utc(info.context.clock())


def should_mask(error: GraphQLError) -> bool:
    """Hide unexpected resolver failures; keep request, validation, and input errors."""
    return error.path is not None and not isinstance(error.original_error, InputError)


def build_schema(settings: Settings) -> strawberry.Schema:
    limits = Limits(
        max_depth=settings.max_depth,
        max_cost=settings.max_cost,
        sizes={
            ("Query", "calls"): Size("first", 50, settings.max_page_size),
            ("Query", "mapCalls"): Size("limit", 2000, settings.max_map_results),
            ("Summary", "types"): Size("limit", 10, MAX_TYPE_ENTRIES),
        },
        sized_children=frozenset(
            {("CallPage", "nodes"), ("MapCalls", "calls"), ("TypeBreakdown", "entries")}
        ),
    )
    return strawberry.Schema(
        query=Query,
        config=StrawberryConfig(
            scalar_map={
                EpochMilliseconds: strawberry.scalar(
                    name="EpochMilliseconds",
                    description="Milliseconds since 1970-01-01T00:00:00Z, as published.",
                    serialize=int,
                )
            }
        ),
        extensions=[
            lambda: MaxAliasesLimiter(max_alias_count=settings.max_aliases),
            lambda: MaxTokensLimiter(max_token_count=settings.max_tokens),
            lambda: DocumentLimits(limits),
            lambda: MaskErrors(should_mask_error=should_mask),
        ],
    )
