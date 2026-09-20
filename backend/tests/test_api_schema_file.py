"""The committed SDL is the contract the frontend and API collections are written against."""

from pathlib import Path

from panel.api.schema import build_schema
from panel.settings import Settings

COMMITTED = Path(__file__).parents[2] / "docs" / "schema.graphql"


def test_committed_schema_matches_the_served_schema() -> None:
    served = str(build_schema(Settings())) + "\n"
    assert COMMITTED.read_text() == served, (
        "Stale docs/schema.graphql: run uv run panel schema > docs/schema.graphql"
    )
