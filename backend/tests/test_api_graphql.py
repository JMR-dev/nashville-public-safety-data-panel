"""The read-only GraphQL contract, exercised over HTTP against fixture-backed SQLite."""

from pathlib import Path
from typing import Any

import pytest

from panel.store import SourceState, Writer
from tests.api_support import (
    HOUR,
    PROVENANCE,
    ROWS,
    T0,
    WINDOW,
    api_client,
    data,
    error_messages,
    graphql,
    iso,
    populate,
    settings_for,
)
from tests.support import SOURCE, ManualClock, call_row

FEED = """
query Feed($filter: CallFilter!, $first: Int!, $after: String, $asOf: Int) {
  calls(filter: $filter, first: $first, after: $after, asOfVersion: $asOf) {
    asOfVersion
    pageInfo { endCursor hasNextPage }
    nodes {
      id receivedAt hasCoordinates sourcePresent removedAt
      record { OBJECTID Event_Number ZONE_ Call_Received Tencode_Description }
    }
  }
}
"""

RECORD_FIELDS = " ".join(name for name in ROWS[0] if name != "Priority")


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def generation(database: Path, clock: ManualClock) -> int:
    return populate(database, clock)


def ids(page: dict[str, Any]) -> list[int]:
    return [node["record"]["OBJECTID"] for node in page["nodes"]]


async def feed(
    database: Path, clock: ManualClock, filter: dict[str, Any], **variables: Any
) -> dict[str, Any]:
    async with api_client(settings_for(database), clock) as client:
        variables.setdefault("first", 50)
        result = await data(client, FEED, {"filter": filter, **variables})
        return result["calls"]


async def test_calls_are_newest_first_with_upstream_and_derived_fields(
    database: Path, clock: ManualClock, generation: int
) -> None:
    page = await feed(database, clock, WINDOW)
    assert ids(page) == [5, 1, 2, 3]
    assert page["asOfVersion"] == 2
    assert page["pageInfo"]["hasNextPage"] is False
    newest = page["nodes"][0]
    assert newest["id"] == f"{generation}:5"
    assert newest["receivedAt"] == iso(T0 - HOUR) == "2026-09-19T11:00:00+00:00"
    assert newest["record"] == {
        "OBJECTID": 5,
        "Event_Number": ROWS[4]["Event_Number"],
        "ZONE_": "31",
        "Call_Received": T0 - HOUR,
        "Tencode_Description": "3",
    }
    assert [node["hasCoordinates"] for node in page["nodes"]] == [True, True, False, False]


async def test_cursor_pagination_visits_every_call_once(
    database: Path, clock: ManualClock, generation: int
) -> None:
    seen: list[int] = []
    after = None
    while True:
        page = await feed(database, clock, WINDOW, first=1, after=after)
        seen += ids(page)
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    assert seen == [5, 1, 2, 3]


@pytest.mark.parametrize(
    ("narrowing", "expected"),
    [
        ({"ZONE_": ["15"]}, [1, 3]),
        ({"ZONE_": ["15", "23"]}, [1, 2, 3]),
        ({"Sector": ["C1"], "Tencode_Description": ["ALARM - BURGLAR"]}, [1, 3]),
        ({"Disposition_Description": ["ASSISTED CITIZEN"]}, [5, 1]),
        ({"since": iso(T0 - HOUR), "until": iso(T0)}, [5, 1]),
        ({"since": iso(T0 - 3 * HOUR), "until": iso(T0 - HOUR)}, [2, 3]),
    ],
)
async def test_filters_use_upstream_values_and_a_half_open_date_range(
    database: Path,
    clock: ManualClock,
    generation: int,
    narrowing: dict[str, Any],
    expected: list[int],
) -> None:
    assert ids(await feed(database, clock, {**WINDOW, **narrowing})) == expected


async def test_feed_snapshot_pins_arrivals_and_removals_until_refreshed(
    database: Path, clock: ManualClock, generation: int
) -> None:
    writer = Writer(database, clock=clock)
    backdated = call_row(7, Call_Received=T0 - int(2.5 * HOUR))
    writer.commit_page(generation, [backdated])
    writer.mark_removed(generation, [2])
    writer.close()

    pinned = await feed(database, clock, WINDOW, asOf=2)
    assert (ids(pinned), pinned["asOfVersion"]) == ([5, 1, 2, 3], 2)

    async with api_client(settings_for(database), clock) as client:
        count = await data(
            client,
            "query($f: CallFilter!) { newCallCount(filter: $f, sinceVersion: 2) }",
            {"f": WINDOW},
        )
    assert count == {"newCallCount": 1}

    current = await feed(database, clock, WINDOW)
    assert (ids(current), current["asOfVersion"]) == ([5, 1, 7, 3], 4)

    everything = await feed(database, clock, {**WINDOW, "includeRemoved": True})
    assert ids(everything) == [5, 1, 2, 7, 3]
    removed = everything["nodes"][2]
    assert (removed["sourcePresent"], removed["removedAt"]) == (False, iso(T0))


async def test_call_detail_has_every_upstream_field_and_unknown_attributes(
    database: Path, clock: ManualClock, generation: int
) -> None:
    query = f"""
    query($id: ID!) {{
      call(id: $id) {{
        id generation firstSeenAt lastSeenAt lastChangedAt additionalAttributes
        record {{ {RECORD_FIELDS} }}
      }}
    }}
    """
    async with api_client(settings_for(database), clock) as client:
        found = (await data(client, query, {"id": f"{generation}:5"}))["call"]
        missing = await data(client, query, {"id": f"{generation}:99"})
        malformed = await error_messages(client, query, {"id": "5"})
    expected = {name: value for name, value in ROWS[4].items() if name != "Priority"}
    assert found["record"] == expected
    assert found["additionalAttributes"] == {"Priority": "HIGH"}
    assert found["generation"] == generation
    assert found["firstSeenAt"] == found["lastSeenAt"] == found["lastChangedAt"] == iso(T0)
    assert missing == {"call": None}
    assert malformed == ["Invalid call id"]


async def test_map_results_are_bounded_and_count_calls_without_coordinates(
    database: Path, clock: ManualClock, generation: int
) -> None:
    query = """
    query($f: CallFilter!, $limit: Int!) {
      mapCalls(filter: $f, limit: $limit) {
        matching withCoordinates truncated
        calls { id record { Latitude Longitude } }
      }
    }
    """
    async with api_client(settings_for(database), clock) as client:
        full = (await data(client, query, {"f": WINDOW, "limit": 10}))["mapCalls"]
        bounded = (await data(client, query, {"f": WINDOW, "limit": 1}))["mapCalls"]
    assert [call["id"] for call in full["calls"]] == [f"{generation}:5", f"{generation}:1"]
    assert (full["matching"], full["withCoordinates"], full["truncated"]) == (4, 2, False)
    assert full["calls"][0]["record"] == {"Latitude": 36.16, "Longitude": -86.78}
    assert ([call["id"] for call in bounded["calls"]], bounded["truncated"]) == (
        [f"{generation}:5"],
        True,
    )


async def test_summary_counts_source_records_by_type_and_hour(
    database: Path, clock: ManualClock, generation: int
) -> None:
    query = """
    query($f: CallFilter!) {
      summary(filter: $f) {
        total withCoordinates withoutCoordinates
        types(limit: 2) { otherCount entries { Tencode_Description count } }
        hourly { start count }
      }
    }
    """
    async with api_client(settings_for(database), clock) as client:
        summary = (await data(client, query, {"f": WINDOW}))["summary"]
    assert (summary["total"], summary["withCoordinates"], summary["withoutCoordinates"]) == (
        4,
        2,
        2,
    )
    assert summary["types"] == {
        "entries": [
            {"Tencode_Description": "ALARM - BURGLAR", "count": 2},
            {"Tencode_Description": "3", "count": 1},
        ],
        "otherCount": 1,
    }
    assert summary["hourly"] == [
        {"start": iso(T0 - 3 * HOUR), "count": 1},
        {"start": iso(T0 - 2 * HOUR), "count": 1},
        {"start": iso(T0 - HOUR), "count": 2},
    ]


async def test_filter_values_are_distinct_sorted_and_scoped_to_the_dates(
    database: Path, clock: ManualClock, generation: int
) -> None:
    query = """
    query($since: DateTime!, $until: DateTime!) {
      filterValues(since: $since, until: $until) {
        ZONE_ Sector Tencode_Description Disposition_Description
      }
    }
    """
    async with api_client(settings_for(database), clock) as client:
        values = (await data(client, query, WINDOW))["filterValues"]
    assert values == {
        "ZONE_": ["15", "23", "31"],
        "Sector": ["C1", "N2"],
        "Tencode_Description": ["3", "ALARM - BURGLAR", "TRAFFIC VIOLATION"],
        "Disposition_Description": ["ASSISTED CITIZEN", "REPORT TAKEN"],
    }


async def test_source_status_separates_polling_changes_and_source_freshness(
    database: Path, clock: ManualClock, generation: int
) -> None:
    query = """
    {
      dataVersion serverTime
      sourceStatus {
        source state detail lastPollAt lastChangeAt latestCallReceivedAt upstreamEditedAt
        degradedSince retryAt windowReconciledAt fullReconciledAt
        generation { id url serviceItemId layerName reason createdAt active }
        backfill { boundary rangesTotal rangesCompleted fraction completedAt }
      }
    }
    """
    clock.advance(2_000)
    async with api_client(settings_for(database), clock) as client:
        result = await data(client, query)
    assert (result["dataVersion"], result["serverTime"]) == (2, iso(T0 + 2_000))
    [status] = result["sourceStatus"]
    assert status == {
        "source": SOURCE,
        "state": "BACKFILLING",
        "detail": "Backfilling 2 ranges",
        "lastPollAt": iso(T0),
        "lastChangeAt": iso(T0),
        "latestCallReceivedAt": iso(T0 - HOUR),
        "upstreamEditedAt": iso(T0 - 5 * 60_000),
        "degradedSince": None,
        "retryAt": None,
        "windowReconciledAt": None,
        "fullReconciledAt": None,
        "generation": {
            "id": generation,
            "url": status["generation"]["url"],
            "serviceItemId": "item-1",
            "layerName": "MNPD_Calls_for_Service",
            "reason": "initial",
            "createdAt": iso(T0),
            "active": True,
        },
        "backfill": {
            "boundary": 6,
            "rangesTotal": 2,
            "rangesCompleted": 1,
            "fraction": pytest.approx(5 / 6),
            "completedAt": None,
        },
    }


async def test_status_before_the_worker_has_started_is_empty(
    database: Path, clock: ManualClock
) -> None:
    writer = Writer(database, clock=clock)
    writer.initialize()
    writer.close()
    async with api_client(settings_for(database), clock) as client:
        result = await data(client, "{ dataVersion sourceStatus { source } }")
    assert result == {"dataVersion": 0, "sourceStatus": []}


async def test_status_distinguishes_unstarted_pending_and_empty_sources(
    database: Path, clock: ManualClock
) -> None:
    writer = Writer(database, clock=clock)
    writer.initialize()
    writer.set_status("a-starting", state=SourceState.STARTING)
    pending = writer.start_generation("b-pending", PROVENANCE, "initial").id
    writer.set_status("b-pending", generation=pending, state=SourceState.BACKFILLING)
    empty = writer.start_generation("c-empty", PROVENANCE, "initial").id
    writer.capture_boundary(empty, 0, [])
    writer.complete_backfill(empty)
    writer.set_status("c-empty", generation=empty, state=SourceState.LIVE)
    writer.close()
    query = """
    { sourceStatus {
        source state latestCallReceivedAt generation { id }
        backfill { boundary rangesTotal rangesCompleted fraction completedAt }
    } }
    """
    async with api_client(settings_for(database), clock) as client:
        statuses = (await data(client, query))["sourceStatus"]
    assert statuses == [
        {
            "source": "a-starting",
            "state": "STARTING",
            "latestCallReceivedAt": None,
            "generation": None,
            "backfill": None,
        },
        {
            "source": "b-pending",
            "state": "BACKFILLING",
            "latestCallReceivedAt": None,
            "generation": {"id": pending},
            "backfill": None,
        },
        {
            "source": "c-empty",
            "state": "LIVE",
            "latestCallReceivedAt": None,
            "generation": {"id": empty},
            "backfill": {
                "boundary": 0,
                "rangesTotal": 0,
                "rangesCompleted": 0,
                "fraction": 1.0,
                "completedAt": iso(T0),
            },
        },
    ]


INVALID = {
    "range-too-long": (
        {"filter": {"since": iso(T0 - 93 * 24 * HOUR), "until": iso(T0)}},
        "The date range cannot exceed 92 days",
    ),
    "reversed-range": (
        {"filter": {"since": iso(T0), "until": iso(T0 - HOUR)}},
        "since must be earlier than until",
    ),
    "naive-time": (
        {"filter": {"since": "2026-09-19T00:00:00", "until": iso(T0)}},
        "since must include a time zone",
    ),
    "page-too-small": ({"first": 0}, "first must be between 1 and 200"),
    "page-too-large": ({"first": 201}, "first must be between 1 and 200"),
    "bad-cursor": ({"after": "not-a-cursor"}, "Invalid cursor"),
    "cursor-shape": ({"after": "WzEsIDJd"}, "Invalid cursor"),
    "negative-version": ({"asOf": -1}, "asOfVersion must not be negative"),
}


@pytest.mark.parametrize(("variables", "message"), list(INVALID.values()), ids=list(INVALID))
async def test_invalid_feed_requests_explain_the_problem(
    database: Path,
    clock: ManualClock,
    generation: int,
    variables: dict[str, Any],
    message: str,
) -> None:
    request = {"filter": WINDOW, "first": 50, **variables}
    async with api_client(settings_for(database), clock) as client:
        assert await error_messages(client, FEED, request) == [message]


@pytest.mark.parametrize(
    ("query", "message"),
    [
        (
            "query($f: CallFilter!) { mapCalls(filter: $f, limit: 5001) { matching } }",
            "limit must be between 1 and 5000",
        ),
        (
            "query($f: CallFilter!) { summary(filter: $f) { types(limit: 0) { otherCount } } }",
            "limit must be between 1 and 100",
        ),
        (
            "query($f: CallFilter!) { newCallCount(filter: $f, sinceVersion: -1) }",
            "sinceVersion must not be negative",
        ),
    ],
    ids=["map-limit", "type-limit", "negative-since-version"],
)
async def test_invalid_bounded_requests_explain_the_problem(
    database: Path, clock: ManualClock, generation: int, query: str, message: str
) -> None:
    async with api_client(settings_for(database), clock) as client:
        assert await error_messages(client, query, {"f": WINDOW}) == [message]


async def test_filter_value_dates_are_validated(
    database: Path, clock: ManualClock, generation: int
) -> None:
    query = "query($s: DateTime!, $u: DateTime!) { filterValues(since: $s, until: $u) { ZONE_ } }"
    async with api_client(settings_for(database), clock) as client:
        messages = await error_messages(client, query, {"s": iso(T0), "u": iso(T0 - HOUR)})
    assert messages == ["since must be earlier than until"]


@pytest.mark.parametrize(
    ("limits", "query", "fragment"),
    [
        (
            {"max_depth": 2},
            "query($f: CallFilter!) { calls(filter: $f) { nodes { record { OBJECTID } } } }",
            "Query depth 3 exceeds the maximum of 2",
        ),
        (
            {"max_aliases": 2},
            "{ a: dataVersion b: dataVersion c: dataVersion }",
            "3 aliases found. Allowed: 2",
        ),
        (
            {"max_tokens": 10},
            "{ a: dataVersion b: dataVersion c: dataVersion }",
            "more than 10 tokens",
        ),
        (
            {"max_cost": 100},
            "query($f: CallFilter!) { calls(filter: $f, first: 60) { nodes { id } } }",
            "Query cost 121 exceeds the maximum of 100",
        ),
        (
            {"max_cost": 300},
            "query($f: CallFilter!, $n: Int!) { calls(filter: $f, first: $n) { nodes { id } } }",
            "Query cost 301 exceeds the maximum of 300",
        ),
        (
            {"max_cost": 300},
            "query($f: CallFilter!, $m: Int = 5) { calls(filter: $f, first: $m) { nodes { id } } }",
            "Query cost 401 exceeds the maximum of 300",
        ),
    ],
    ids=["depth", "aliases", "tokens", "cost-literal", "cost-variable", "cost-unsupplied-variable"],
)
async def test_expensive_documents_are_rejected_before_execution(
    database: Path,
    clock: ManualClock,
    generation: int,
    limits: dict[str, int],
    query: str,
    fragment: str,
) -> None:
    async with api_client(settings_for(database, **limits), clock) as client:
        messages = await error_messages(client, query, {"f": WINDOW, "n": 150})
    assert len(messages) == 1
    assert fragment in messages[0]


async def test_limits_use_the_variables_sent_with_the_request(
    database: Path, clock: ManualClock, generation: int
) -> None:
    # The dashboard's map query: 1 + limit x (3 counts + calls(1 + id + receivedAt + record(6))).
    query = """
    query($f: CallFilter!, $limit: Int!) {
      mapCalls(filter: $f, limit: $limit) {
        matching withCoordinates truncated
        calls { id receivedAt record { Latitude Longitude Tencode_Description Block Street_Name } }
      }
    }
    """
    async with api_client(settings_for(database), clock) as client:
        assert await data(client, query, {"f": WINDOW, "limit": 2000})
        messages = await error_messages(client, query, {"f": WINDOW, "limit": 5000})
    assert messages == ["Query cost 60001 exceeds the maximum of 50000"]


async def test_limits_count_fragments_and_ignore_introspection(
    database: Path, clock: ManualClock, generation: int
) -> None:
    fragment_query = """
    query($f: CallFilter!) { calls(filter: $f, first: 10) { ...page } }
    fragment page on CallPage { nodes { ... on Call { id } } }
    """
    inline_query = """
    query($f: CallFilter!) { ... { calls(filter: $f, first: 10) { nodes { id } } } }
    """
    async with api_client(settings_for(database, max_cost=21, max_depth=2), clock) as client:
        assert await data(client, "{ __schema { types { name fields { name } } } }")
        assert await data(client, fragment_query, {"f": WINDOW})
        assert await data(client, inline_query, {"f": WINDOW})
    async with api_client(settings_for(database, max_cost=20, max_depth=1), clock) as client:
        messages = await error_messages(client, fragment_query, {"f": WINDOW})
        inline_messages = await error_messages(client, inline_query, {"f": WINDOW})
    expected = ["Query depth 2 exceeds the maximum of 1", "Query cost 21 exceeds the maximum of 20"]
    assert messages == inline_messages == expected


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("{ ...missing }", "Unknown fragment 'missing'."),
        (
            "fragment a on Query { dataVersion ...a } { ...a }",
            "Cannot spread fragment 'a' within itself.",
        ),
        ("{ ... on Missing { dataVersion } }", "Unknown type 'Missing'."),
        ("fragment f on Missing { dataVersion } { ...f }", "Unknown type 'Missing'."),
        ("{ nope }", "Cannot query field 'nope' on type 'Query'."),
        (
            'query($f: CallFilter!) { calls(filter: $f, first: "ten") { nodes { id } } }',
            'Int cannot represent non-integer value: "ten"',
        ),
    ],
    ids=[
        "unknown-fragment",
        "fragment-cycle",
        "unknown-inline-type",
        "unknown-fragment-type",
        "unknown-field",
        "non-integer-size",
    ],
)
async def test_invalid_documents_get_standard_validation_errors(
    database: Path, clock: ManualClock, generation: int, document: str, message: str
) -> None:
    async with api_client(settings_for(database), clock) as client:
        assert await error_messages(client, document) == [message]


async def test_the_schema_is_read_only(database: Path, clock: ManualClock, generation: int) -> None:
    async with api_client(settings_for(database), clock) as client:
        mutation = await error_messages(client, "mutation { dataVersion }")
        subscription = await error_messages(client, "subscription { dataVersion }")
    assert mutation == ["Schema is not configured to execute mutation operation."]
    assert subscription == ["Schema is not configured to execute subscription operation."]


async def test_queries_are_also_accepted_over_get(
    database: Path, clock: ManualClock, generation: int
) -> None:
    async with api_client(settings_for(database), clock) as client:
        response = await client.get("/graphql", params={"query": "{ dataVersion }"})
    assert response.json() == {"data": {"dataVersion": 2}}


async def test_unexpected_failures_do_not_leak_internals(
    empty_database: Path, clock: ManualClock
) -> None:
    async with api_client(settings_for(empty_database), clock) as client:
        body = await graphql(client, "{ dataVersion }")
    assert body["data"] is None
    assert [error["message"] for error in body["errors"]] == ["Unexpected error."]
