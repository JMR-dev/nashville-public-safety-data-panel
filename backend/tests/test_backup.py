"""Backups are consistent, restorable copies, kept to a fixed number."""

import sqlite3
from pathlib import Path

from panel.backup import copy_database, prune, stored_backups, take_backup
from panel.store import Progress, Provenance, Writer
from tests.support import URL, ManualClock, call_row

PROVENANCE = Provenance(url=URL, service_item_id="item-1", layer_name="MNPD_Calls_for_Service")


def populated(database: Path, clock: ManualClock) -> Writer:
    writer = Writer(database, clock=clock)
    writer.initialize()
    generation = writer.start_generation("mnpd-calls", PROVENANCE, "initial").id
    writer.commit_page(generation, [call_row(1), call_row(2)], Progress("live", cursor=2))
    return writer


def test_a_backup_taken_while_reading_restores_completely(
    database: Path, clock: ManualClock, tmp_path: Path
) -> None:
    writer = populated(database, clock)
    reader = sqlite3.connect(database)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM police_calls").fetchone()

    target = tmp_path / "backups" / "panel-restored.sqlite"
    copy_database(database, target)
    reader.rollback()
    reader.close()
    writer.close()

    restored = Writer(target, clock=clock)
    assert restored.checkpoint(1, "live") == 2
    assert restored.active_generation("mnpd-calls") is not None
    restored.close()
    assert not target.with_name(target.name + ".partial").exists()


def test_backups_are_named_by_time_and_kept_to_a_limit(
    database: Path, clock: ManualClock, tmp_path: Path
) -> None:
    writer = populated(database, clock)
    directory = tmp_path / "backups"
    taken: list[Path] = []
    for _ in range(9):
        taken.append(take_backup(database, directory, keep=7, now=clock.now))
        clock.advance(86_400_000)
    writer.close()

    assert taken[0].name == "panel-20260910T002640000Z.sqlite"
    kept = stored_backups(directory)
    assert len(kept) == 7
    assert kept == taken[2:]
    assert kept == sorted(kept)


def test_pruning_keeps_everything_when_there_are_fewer_than_the_limit(
    database: Path, clock: ManualClock, tmp_path: Path
) -> None:
    writer = populated(database, clock)
    directory = tmp_path / "backups"
    take_backup(database, directory, keep=7, now=clock.now)
    writer.close()
    assert prune(directory, keep=7) == []
    assert len(stored_backups(directory)) == 1
