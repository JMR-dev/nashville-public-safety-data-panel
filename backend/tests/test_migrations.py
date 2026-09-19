"""The explicit migration builds exactly the schema the application declares."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from panel.database import current_revision, downgrade, head_revision, open_engine, upgrade
from panel.tables import SOURCE_FIELDS, metadata

SQLITE_AFFINITY = {
    "esriFieldTypeOID": "INTEGER",
    "esriFieldTypeInteger": "INTEGER",
    "esriFieldTypeDate": "INTEGER",
    "esriFieldTypeDouble": "FLOAT",
    "esriFieldTypeString": "VARCHAR",
}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_engine(tmp_path / "panel.sqlite")
    yield engine
    engine.dispose()


def test_migrated_database_matches_declared_tables(engine: Engine) -> None:
    upgrade(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []


def test_calls_table_keeps_upstream_names_and_types(engine: Engine) -> None:
    upgrade(engine)
    columns = {
        column["name"]: str(column["type"])
        for column in inspect(engine).get_columns("police_calls")
    }
    for name, esri_type in SOURCE_FIELDS.items():
        assert columns[name] == SQLITE_AFFINITY[esri_type], name


def test_downgrade_removes_schema_and_upgrade_restores_it(engine: Engine) -> None:
    upgrade(engine)
    downgrade(engine, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    upgrade(engine)
    assert set(inspect(engine).get_table_names()) >= set(metadata.tables)


def test_connections_use_wal_busy_timeout_and_foreign_keys(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_revision_is_tracked_from_unmigrated_to_head(engine: Engine) -> None:
    with engine.connect() as connection:
        assert current_revision(connection) is None
    upgrade(engine)
    with engine.connect() as connection:
        assert current_revision(connection) == head_revision() == "0001"
