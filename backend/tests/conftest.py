"""Shared fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from panel.store import Writer
from tests.support import ManualClock


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "data" / "panel.sqlite"


@pytest.fixture
def writer(database: Path, clock: ManualClock) -> Iterator[Writer]:
    store = Writer(database, clock=clock)
    store.initialize()
    yield store
    store.close()


@pytest.fixture
def empty_database(database: Path) -> Path:
    """A database file that exists but has never been migrated."""
    database.parent.mkdir(parents=True)
    database.touch()
    return database


@pytest.fixture
def unreadable_database(database: Path) -> Path:
    """A directory where the database file should be."""
    database.mkdir(parents=True)
    return database
