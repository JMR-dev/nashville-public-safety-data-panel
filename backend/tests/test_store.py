"""Ingestion persistence: pages, checkpoints, provenance, and presence commit atomically."""

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from panel.store import Progress, Provenance, SourceState, Writer, WriterBusy
from panel.tables import calls, checkpoints, source_status
from tests.support import SOURCE, URL, ManualClock, call_row

PROVENANCE = Provenance(url=URL, service_item_id="item-1", layer_name="MNPD_Calls_for_Service")


def stored(writer: Writer, generation: int) -> dict[int, dict[str, Any]]:
    with writer.engine.connect() as connection:
        rows = connection.execute(select(calls).where(calls.c.generation == generation))
        return {row["OBJECTID"]: dict(row) for row in rows.mappings()}


def status(writer: Writer) -> dict[str, Any]:
    with writer.engine.connect() as connection:
        row = connection.execute(
            select(source_status).where(source_status.c.source == SOURCE)
        ).mappings()
        return dict(row.one())


def test_page_rows_and_checkpoint_survive_restart(
    writer: Writer, database: Path, clock: ManualClock
) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    row = call_row(1, unexpected_attribute="kept")
    result = writer.commit_page(generation, [row], Progress("live", cursor=1))
    assert (result.inserted, result.changed, result.unchanged) == (1, 0, 0)
    writer.close()

    reopened = Writer(database, clock=clock)
    assert reopened.checkpoint(generation, "live") == 1
    saved = stored(reopened, generation)[1]
    assert saved["Event_Number"] == row["Event_Number"]
    assert saved["Call_Received"] == row["Call_Received"]
    assert saved["raw"] == row
    assert saved["raw"]["unexpected_attribute"] == "kept"
    reopened.close()


def test_failed_page_leaves_neither_rows_nor_checkpoint(writer: Writer) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.commit_page(generation, [call_row(1)], Progress("live", cursor=1))
    malformed = {"Event_Number": "PD without an OBJECTID"}
    with pytest.raises(KeyError):
        writer.commit_page(generation, [call_row(2), malformed], Progress("live", cursor=3))
    assert writer.checkpoint(generation, "live") == 1
    assert set(stored(writer, generation)) == {1}
    assert writer.data_version() == 1


def test_unchanged_records_confirm_presence_without_a_new_version(
    writer: Writer, clock: ManualClock
) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.commit_page(generation, [call_row(1)])
    first_change = status(writer)["last_change_at"]
    clock.advance(60_000)
    result = writer.commit_page(generation, [call_row(1)])
    assert (result.inserted, result.changed, result.unchanged) == (0, 0, 1)
    assert result.version == 1
    assert writer.data_version() == 1
    row = stored(writer, generation)[1]
    assert row["last_seen_at"] == clock.now
    assert row["last_changed_at"] == first_change
    assert status(writer)["last_poll_at"] == clock.now
    assert status(writer)["last_change_at"] == first_change


def test_changed_record_is_updated_in_place_with_a_new_version(
    writer: Writer, clock: ManualClock
) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.commit_page(generation, [call_row(1, Disposition_Description=None)])
    first_seen = clock.now
    clock.advance(5_000)
    result = writer.commit_page(generation, [call_row(1, Disposition_Description="REPORT TAKEN")])
    assert result.changed == 1
    assert result.version == 2
    row = stored(writer, generation)[1]
    assert row["Disposition_Description"] == "REPORT TAKEN"
    assert row["raw"]["Disposition_Description"] == "REPORT TAKEN"
    assert (row["first_seen_at"], row["last_changed_at"]) == (first_seen, clock.now)
    assert (row["first_seen_version"], row["changed_version"]) == (1, 2)
    assert status(writer)["last_change_at"] == clock.now


def test_empty_poll_is_recorded_as_a_successful_poll_only(
    writer: Writer, clock: ManualClock
) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    clock.advance(500)
    result = writer.commit_page(generation, [], Progress("live", cursor=0))
    assert result.version == 0
    assert status(writer)["last_poll_at"] == clock.now
    assert status(writer)["last_change_at"] is None


def test_event_number_is_not_a_unique_key(writer: Writer) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.commit_page(
        generation,
        [
            call_row(1, Event_Number="PD1", Unit_Dispatched="A"),
            call_row(2, Event_Number="PD1", Unit_Dispatched="B"),
        ],
    )
    assert {row["Unit_Dispatched"] for row in stored(writer, generation).values()} == {"A", "B"}


def test_new_generation_retires_the_old_and_namespaces_identifiers(
    writer: Writer, clock: ManualClock
) -> None:
    first = writer.start_generation(SOURCE, PROVENANCE, "initial")
    writer.commit_page(first.id, [call_row(1, Event_Number="PD-OLD")])
    clock.advance(1_000)
    second = writer.start_generation(SOURCE, PROVENANCE, "identifier_reset")
    writer.commit_page(second.id, [call_row(1, Event_Number="PD-NEW")])

    assert writer.active_generation(SOURCE) == second
    assert (second.reason, second.url, second.service_item_id) == (
        "identifier_reset",
        URL,
        "item-1",
    )
    retired = writer.generation(first.id)
    assert (retired.active, retired.retired_at) == (False, clock.now)
    assert stored(writer, first.id)[1]["Event_Number"] == "PD-OLD"
    assert stored(writer, second.id)[1]["Event_Number"] == "PD-NEW"
    assert writer.active_generation("another-source") is None


def test_backfill_completes_only_after_every_range_completes(writer: Writer) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.capture_boundary(generation, 10, [(0, 5), (5, 10)])
    writer.capture_boundary(generation, 99, [(0, 99)])
    assert writer.generation(generation).boundary == 10
    assert [(r.lower, r.upper, r.cursor, r.completed) for r in writer.ranges(generation)] == [
        (0, 5, 0, False),
        (5, 10, 5, False),
    ]

    writer.commit_page(generation, [call_row(3)], Progress("backfill:0-5", cursor=5, complete=True))
    assert not writer.complete_backfill(generation)
    assert writer.generation(generation).backfill_completed_at is None
    assert writer.checkpoint(generation, "live") is None

    writer.commit_page(generation, [], Progress("backfill:5-10", cursor=10, complete=True))
    assert writer.complete_backfill(generation)
    assert writer.generation(generation).backfill_completed_at is not None
    assert writer.checkpoint(generation, "live") == 10


def test_live_checkpoint_is_not_rewound_by_backfill_completion(writer: Writer) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.capture_boundary(generation, 10, [(0, 10)])
    writer.commit_page(generation, [], Progress("backfill:0-10", cursor=10, complete=True))
    writer.commit_page(generation, [call_row(12)], Progress("live", cursor=12))
    assert writer.complete_backfill(generation)
    assert writer.checkpoint(generation, "live") == 12


def test_removed_records_are_retained_and_can_reappear(writer: Writer, clock: ManualClock) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.commit_page(generation, [call_row(1), call_row(2)])
    clock.advance(1_000)
    assert writer.mark_removed(generation, [2, 3]) == 1
    assert writer.mark_removed(generation, []) == 0
    assert writer.data_version() == 2
    row = stored(writer, generation)[2]
    assert (row["source_present"], row["removed_at"]) == (False, clock.now)
    assert writer.present_ids(generation, upper=10) == {1}

    clock.advance(1_000)
    result = writer.commit_page(generation, [call_row(2)])
    assert result.changed == 1
    row = stored(writer, generation)[2]
    assert (row["source_present"], row["removed_at"], row["changed_version"]) == (True, None, 3)


def test_present_ids_are_scoped_by_boundary_and_received_time(writer: Writer) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    other = writer.start_generation("another-source", PROVENANCE, "initial").id
    writer.commit_page(
        generation,
        [
            call_row(1, Call_Received=1_000),
            call_row(2, Call_Received=5_000),
            call_row(3, Call_Received=9_000),
            call_row(4, Call_Received=None),
        ],
    )
    writer.commit_page(other, [call_row(2, Call_Received=5_000)])
    assert writer.present_ids(generation, upper=4) == {1, 2, 3, 4}
    assert writer.present_ids(generation, upper=2) == {1, 2}
    assert writer.present_ids(generation, upper=4, since=5_000) == {2, 3}


def test_schema_snapshots_are_recorded_only_when_the_schema_changes(writer: Writer) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    fields = [{"name": "OBJECTID", "type": "esriFieldTypeOID"}]
    assert writer.record_schema(generation, fields, compatible=True, problems=[])
    assert not writer.record_schema(generation, fields, compatible=True, problems=[])
    changed = [*fields, {"name": "Priority", "type": "esriFieldTypeString"}]
    assert writer.record_schema(generation, changed, compatible=True, problems=[])
    latest = writer.latest_schema(generation)
    assert latest is not None
    assert latest["fields"] == changed
    assert writer.latest_schema(generation + 1) is None


def test_status_changes_merge_into_one_row(writer: Writer, clock: ManualClock) -> None:
    writer.set_status(SOURCE, state=SourceState.BACKFILLING)
    writer.set_status(SOURCE, upstream_edit_at=123, detail="Backfilling 2 ranges")
    row = status(writer)
    assert (row["state"], row["upstream_edit_at"], row["detail"]) == (
        "backfilling",
        123,
        "Backfilling 2 ranges",
    )
    assert row["updated_at"] == clock.now
    writer.set_status(SOURCE, state=SourceState.LIVE, detail=None)
    assert (status(writer)["state"], status(writer)["detail"]) == ("live", None)


def test_only_one_writer_may_open_a_database(database: Path, clock: ManualClock) -> None:
    first = Writer(database, clock=clock)
    first.lock()
    second = Writer(database, clock=clock)
    with pytest.raises(WriterBusy):
        second.lock()
    first.close()
    second.lock()
    second.close()


def test_online_backup_is_a_complete_restorable_copy(
    writer: Writer, tmp_path: Path, clock: ManualClock
) -> None:
    generation = writer.start_generation(SOURCE, PROVENANCE, "initial").id
    writer.commit_page(generation, [call_row(1), call_row(2)], Progress("live", cursor=2))
    reader = sqlite3.connect(writer.path)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM police_calls").fetchone()
    target = tmp_path / "backups" / "panel.sqlite"
    writer.backup(target)
    reader.rollback()
    reader.close()

    restored = Writer(target, clock=clock)
    assert restored.checkpoint(generation, "live") == 2
    assert set(stored(restored, generation)) == {1, 2}
    with restored.engine.connect() as connection:
        assert connection.execute(select(checkpoints.c.name)).scalars().all() == ["live"]
    restored.close()


def test_default_clock_records_wall_clock_epoch_milliseconds(database: Path) -> None:
    store = Writer(database)
    store.initialize()
    before = time.time_ns() // 1_000_000
    generation = store.start_generation(SOURCE, PROVENANCE, "initial")
    after = time.time_ns() // 1_000_000
    store.close()
    assert before <= generation.created_at <= after
