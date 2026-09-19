"""Backups taken with SQLite's online backup, so a running worker is never interrupted."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PREFIX = "panel-"
SUFFIX = ".sqlite"


def copy_database(database: Path, target: Path) -> None:
    """Copy a consistent snapshot, publishing it only once it is complete."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    with (
        closing(sqlite3.connect(database)) as source,
        closing(sqlite3.connect(partial)) as destination,
    ):
        source.backup(destination)
    partial.replace(target)


def stored_backups(directory: Path) -> list[Path]:
    """Backups oldest first; the names sort in the order they were taken."""
    return sorted(directory.glob(f"{PREFIX}*{SUFFIX}"))


def prune(directory: Path, keep: int) -> list[Path]:
    """Remove all but the newest ``keep`` backups, and report what was removed."""
    removed = stored_backups(directory)[: -keep or None]
    for backup in removed:
        backup.unlink()
    return removed


def take_backup(database: Path, directory: Path, keep: int, now: int) -> Path:
    # Milliseconds keep two backups taken in the same second from overwriting each other.
    stamp = datetime.fromtimestamp(now / 1000, UTC).strftime("%Y%m%dT%H%M%S%f")[:-3]
    target = directory / f"{PREFIX}{stamp}Z{SUFFIX}"
    copy_database(database, target)
    prune(directory, keep)
    return target
