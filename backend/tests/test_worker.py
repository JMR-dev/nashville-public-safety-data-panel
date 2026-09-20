"""Ingestion against a simulated source: backfill, live polling, and failure handling."""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from panel.ingest import LIVE_GAP, SourceError
from panel.store import SourceState, Writer, WriterBusy
from panel.worker import Worker
from tests.fake_source import FakeSource, error_response, unavailable
from tests.fake_time import FakeTime
from tests.support import SOURCE
from tests.worker_support import (
    build,
    generation_id,
    rows,
    run_until,
    state,
    status,
    stored,
)

T0 = 1_789_819_200_000


@pytest.fixture
def clock() -> FakeTime:
    return FakeTime(T0)


@pytest.fixture
def source(clock: FakeTime) -> FakeSource:
    return FakeSource(sleep=clock.sleep)


@pytest.fixture
async def workers(
    database: Path, source: FakeSource, clock: FakeTime
) -> AsyncIterator[Callable[..., Worker]]:
    """Builds workers against the simulated source and closes them afterwards."""
    built: list[Worker] = []

    def factory(**overrides: Any) -> Worker:
        worker = build(database, source, clock, **overrides)
        built.append(worker)
        return worker

    yield factory
    for worker in built:
        await worker.close()


def is_live(writer: Writer) -> bool:
    return state(writer) == SourceState.LIVE


async def test_backfill_collects_every_record_then_goes_live(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(4500)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))

    saved = stored(worker.writer)
    assert len(saved) == 4500
    assert saved[1]["Event_Number"] == source.records[1]["Event_Number"]
    active = worker.writer.active_generation(SOURCE)
    assert active is not None
    assert active.boundary == 4500
    assert active.backfill_completed_at is not None
    assert all(checkpoint.completed for checkpoint in worker.writer.ranges(active.id))
    assert worker.writer.checkpoint(active.id, "live") == 4500
    assert status(worker.writer)["last_poll_at"] is not None


async def test_a_stopped_worker_leaves_the_backfill_where_it_was(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(100_000)
    worker = workers()
    await run_until(worker, lambda: len(stored(worker.writer)) > 0, seconds=20)

    saved = len(stored(worker.writer))
    assert 0 < saved < 100_000
    active = worker.writer.active_generation(SOURCE)
    assert active is not None
    assert active.backfill_completed_at is None
    assert state(worker.writer) != SourceState.LIVE
    # The committed pages are kept, so the next run resumes from them.
    covered = sum(entry.cursor - entry.lower for entry in worker.writer.ranges(active.id))
    assert covered >= saved


async def test_backfill_stays_within_its_concurrency_and_rate(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(40_000)
    worker = workers(backfill_concurrency=4)
    await run_until(worker, lambda: is_live(worker.writer))

    assert source.peak_in_flight <= 4
    assert source.peak_in_flight > 1
    starts = sorted(source.starts)
    assert all(later - earlier >= 0.01 for earlier, later in zip(starts, starts[1:], strict=False))


async def test_backfill_resumes_from_its_checkpoints_after_a_restart(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(20_000)
    worker = workers()

    def collected_some() -> bool:
        return 0 < len(stored(worker.writer)) < 20_000

    await run_until(worker, collected_some)
    pages = source.pages_served
    assert pages > 0
    await worker.close()

    resumed = workers()
    await run_until(resumed, lambda: is_live(resumed.writer))
    assert len(stored(resumed.writer)) == 20_000
    # Ten pages cover the boundary; resuming refetches only what was not committed.
    assert source.pages_served - pages < 10


async def test_a_failed_backfill_keeps_its_progress_and_resumes_after_a_wait(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(40_000)
    source.fail_pages(*[unavailable() for _ in range(5)], after=4)
    worker = workers(backfill_concurrency=2)
    degraded: list[dict[str, Any]] = []

    def has_degraded() -> bool:
        row = status(worker.writer)
        if row.get("state") != SourceState.DEGRADED:
            return False
        degraded.append({**row, "requests": len(source.epochs)})
        return True

    await run_until(worker, has_degraded)

    reported = degraded[0]
    assert reported["degraded_since"] is not None
    assert reported["retry_at"] == reported["degraded_since"] + 60_000
    assert reported["detail"]
    assert worker.backfill_pacer.rate < 100
    active = worker.writer.active_generation(SOURCE)
    assert active is not None
    assert any(checkpoint.cursor > 0 for checkpoint in worker.writer.ranges(active.id))
    assert len(stored(worker.writer)) > 0

    await run_until(worker, lambda: is_live(worker.writer), seconds=60)
    # Nothing was asked of the source before the retry time it published.
    assert source.epochs[reported["requests"]] >= reported["retry_at"]
    assert len(stored(worker.writer)) == 40_000
    assert status(worker.writer)["retry_at"] is None
    # The reduced rate is kept for the rest of the run.
    assert worker.backfill_pacer.rate < 100


async def test_transient_failures_within_a_request_are_retried(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(10)
    source.fail(unavailable("1"), error_response(503))
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    assert len(stored(worker.writer)) == 10
    assert state(worker.writer) == SourceState.LIVE


async def test_live_polling_collects_new_records_after_each_gap(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(10)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    polls = len(source.requests)

    source.publish(2, first=11)
    await run_until(worker, lambda: len(stored(worker.writer)) == 12)
    assert worker.writer.checkpoint(generation_id(worker.writer), "live") == 12

    live_starts = source.starts[polls:]
    gaps = [later - earlier for earlier, later in zip(live_starts, live_starts[1:], strict=False)]
    assert all(gap >= LIVE_GAP for gap in gaps)


async def test_empty_polls_record_a_successful_check_without_a_data_change(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(5)
    worker = workers(window_interval_seconds=5)
    await run_until(worker, lambda: is_live(worker.writer))
    version = worker.writer.data_version()
    polled = status(worker.writer)["last_poll_at"]

    await run_until(worker, lambda: status(worker.writer)["last_poll_at"] > polled)
    assert worker.writer.data_version() == version
    assert status(worker.writer)["last_change_at"] < status(worker.writer)["last_poll_at"]


async def test_an_edited_record_is_updated_in_place(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(5)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    assert stored(worker.writer)[3]["Disposition_Description"] == "ASSISTED CITIZEN"

    source.edit(3, Disposition_Description="REPORT TAKEN")
    await run_until(
        worker,
        lambda: stored(worker.writer)[3]["Disposition_Description"] == "REPORT TAKEN",
        seconds=60,
    )
    assert len(stored(worker.writer)) == 5


async def test_the_source_sees_the_configured_user_agent(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(1)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    assert "NashvillePublicSafetyPanel" in source.headers[0]["user-agent"]


async def test_only_one_worker_may_ingest_into_a_database(
    database: Path, source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(1)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    await worker.close()

    holder = Writer(database, clock=clock.epoch_ms)
    holder.lock()
    blocked = workers()
    with pytest.raises(WriterBusy):
        await blocked.run(None)
    holder.close()


async def test_an_unsupported_query_is_reported_as_a_source_problem(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(3)
    worker = workers()
    with pytest.raises(SourceError, match="Unsupported"):
        await worker.source.page(0, 10, where="Priority = 'HIGH'")


async def test_window_reconciliation_retains_records_the_source_stopped_publishing(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(10)
    worker = workers(window_interval_seconds=5)
    await run_until(worker, lambda: is_live(worker.writer))
    version = worker.writer.data_version()

    source.delete(3)
    await run_until(worker, lambda: status(worker.writer)["window_reconciled_at"] is not None)
    saved = stored(worker.writer)
    assert saved[3]["source_present"] is False
    assert saved[3]["removed_at"] is not None
    assert saved[3]["Event_Number"] == source.records[4]["Event_Number"].replace("4", "3")
    assert saved[4]["source_present"] is True
    assert worker.writer.data_version() > version


async def test_a_reconciliation_that_could_not_finish_removes_nothing(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(3000)
    worker = workers(window_interval_seconds=5)
    await run_until(worker, lambda: is_live(worker.writer))

    source.delete(3)
    # Fail partway through the pass, after its first page.
    source.fail_pages(*[unavailable() for _ in range(5)], after=source.pages_served + 1)
    await run_until(worker, lambda: state(worker.writer) == SourceState.DEGRADED)
    assert stored(worker.writer)[3]["source_present"] is True
    assert status(worker.writer)["window_reconciled_at"] is None

    await run_until(
        worker,
        lambda: status(worker.writer)["window_reconciled_at"] is not None,
        seconds=60,
    )
    assert stored(worker.writer)[3]["source_present"] is False


async def test_the_nightly_pass_covers_records_the_window_cannot_reach(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(5)
    source.publish(3, first=100, received=clock.epoch_ms() - 5 * 86_400_000)
    worker = workers(window_interval_seconds=5)
    await run_until(worker, lambda: is_live(worker.writer))
    assert len(stored(worker.writer)) == 8

    source.delete(100)
    await run_until(worker, lambda: status(worker.writer)["window_reconciled_at"] is not None)
    assert stored(worker.writer)[100]["source_present"] is True

    clock.advance(24 * 3600)
    await run_until(worker, lambda: status(worker.writer)["full_reconciled_at"] is not None)
    assert stored(worker.writer)[100]["source_present"] is False


async def test_an_incompatible_schema_stops_ingestion_until_it_is_compatible(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(5)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))

    published = source.fields
    source.drop_field("ZONE_")
    clock.advance(300)
    await run_until(worker, lambda: state(worker.writer) == SourceState.SCHEMA_INCOMPATIBLE)
    assert "ZONE_" in str(status(worker.writer)["detail"])
    blocked_generation = generation_id(worker.writer)
    snapshot = worker.writer.latest_schema(blocked_generation)
    assert snapshot is not None
    assert snapshot["compatible"] is False
    assert snapshot["problems"] == ["ZONE_ is missing"]

    requests = len(source.requests)
    clock.advance(300)
    await run_until(worker, lambda: len(source.requests) > requests)
    assert state(worker.writer) == SourceState.SCHEMA_INCOMPATIBLE
    assert source.pages_served == source.pages_served

    source.fields = published
    clock.advance(300)
    await run_until(worker, lambda: is_live(worker.writer))
    assert generation_id(worker.writer) == blocked_generation


async def test_a_value_of_the_wrong_type_stops_ingestion(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(4)
    worker = workers(window_interval_seconds=5)
    await run_until(worker, lambda: is_live(worker.writer))

    # The source starts publishing the received time as text instead of epoch milliseconds.
    source.publish(1, first=5)
    source.edit(5, Call_Received="2026-09-19T12:00:00Z")
    await run_until(worker, lambda: state(worker.writer) == SourceState.SCHEMA_INCOMPATIBLE)
    assert "Call_Received" in str(status(worker.writer)["detail"])
    assert 5 not in stored(worker.writer)


async def test_a_replaced_service_starts_a_new_generation_and_keeps_the_old_one(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(4)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    first = generation_id(worker.writer)

    source.replace("item-2")
    source.publish(2)
    clock.advance(300)
    await run_until(worker, lambda: generation_id(worker.writer) != first)
    await run_until(worker, lambda: len(rows(worker.writer)) == 6)

    second = worker.writer.generation(generation_id(worker.writer))
    assert second.reason == "rollover"
    assert second.service_item_id == "item-2"
    retired = worker.writer.generation(first)
    assert (retired.active, retired.retired_at is not None) == (False, True)
    assert len([row for row in rows(worker.writer) if row["generation"] == first]) == 4
    assert len(stored(worker.writer)) == 2


async def test_restarted_identifiers_start_a_new_generation(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(10)
    worker = workers()
    await run_until(worker, lambda: is_live(worker.writer))
    first = generation_id(worker.writer)

    # The same service republished from the beginning.
    source.records.clear()
    source.publish(3)
    clock.advance(300)
    await run_until(worker, lambda: generation_id(worker.writer) != first)
    assert worker.writer.generation(generation_id(worker.writer)).reason == "identifier_reset"
    await run_until(worker, lambda: len(rows(worker.writer)) == 13)
    assert len(stored(worker.writer)) == 3


async def test_the_nightly_pass_waits_for_its_hour(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    # The clock starts at 07:00 in Chicago, well before the configured hour.
    source.publish(4)
    worker = workers(window_interval_seconds=5, nightly_hour=23)
    await run_until(worker, lambda: is_live(worker.writer))

    clock.advance(24 * 3600)
    await run_until(worker, lambda: status(worker.writer)["window_reconciled_at"] is not None)
    assert status(worker.writer)["full_reconciled_at"] is None


async def test_live_polling_comes_before_reconciliation(
    source: FakeSource, clock: FakeTime, workers: Callable[..., Worker]
) -> None:
    source.publish(10)
    worker = workers(window_interval_seconds=5)
    await run_until(worker, lambda: is_live(worker.writer))

    # More new records than one page holds, at the moment a window pass is due.
    source.publish(5000, first=11)
    clock.advance(5)
    await run_until(worker, lambda: len(stored(worker.writer)) > 2000)
    reconciling = [
        request for request in source.requests[-3:] if "Call_Received" in request.get("where", "")
    ]
    assert reconciling == []
    await run_until(worker, lambda: len(stored(worker.writer)) == 5010)
