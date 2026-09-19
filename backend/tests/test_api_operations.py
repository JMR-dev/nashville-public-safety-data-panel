"""The dashboard's own operations must pass this API's validation and limits."""

import re
from pathlib import Path
from typing import Any

import pytest

from tests.api_support import T0, WINDOW, api_client, data, populate, settings_for
from tests.support import ManualClock

FRONTEND = Path(__file__).parents[2] / "frontend" / "src" / "api"
OPERATIONS = {
    path.stem: path.read_text() for path in sorted((FRONTEND / "operations").glob("*.graphql"))
}


def frontend_constant(name: str) -> int:
    """A numeric constant from the dashboard's query module, so the two cannot drift."""
    source = (FRONTEND / "queries.ts").read_text()
    found = re.search(rf"export const {name} = (\d+);", source)
    assert found is not None, name
    return int(found.group(1))


VARIABLES: dict[str, dict[str, Any]] = {
    "anchor": {},
    "status": {},
    "feed": {
        "filter": WINDOW,
        "first": frontend_constant("FEED_PAGE_SIZE"),
        "after": None,
        "asOfVersion": 1,
    },
    "new-calls": {"filter": WINDOW, "sinceVersion": 1},
    "call-detail": {"id": "1:1"},
    "map-calls": {"filter": WINDOW, "limit": frontend_constant("MAP_LIMIT")},
    "summary": {"filter": WINDOW, "types": frontend_constant("TYPE_LIMIT")},
    "filter-values": WINDOW,
}


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


def test_every_frontend_operation_is_covered_by_this_test() -> None:
    assert set(OPERATIONS) == set(VARIABLES)


@pytest.mark.parametrize(("name", "document"), OPERATIONS.items(), ids=list(OPERATIONS))
async def test_frontend_operations_run_within_the_api_limits(
    database: Path, clock: ManualClock, name: str, document: str
) -> None:
    populate(database, clock)
    async with api_client(settings_for(database), clock) as client:
        assert await data(client, document, VARIABLES[name])
