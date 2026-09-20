"""The panel command: migrate, ingest, serve, export the contract, and back up."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from panel import cli
from panel.api.schema import build_schema
from panel.backup import stored_backups
from panel.database import open_engine
from panel.settings import Settings
from panel.store import Writer
from panel.worker import Worker
from tests.support import ManualClock

SCHEMA = Path(__file__).parents[2] / "docs" / "schema.graphql"


@pytest.fixture
def environment(database: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PANEL_DATABASE", str(database))
    monkeypatch.setenv("PANEL_BACKUP_DIR", str(database.parent / "backups"))
    return database


def test_the_command_reports_usage_without_a_subcommand(
    environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([]) == 2
    assert "migrate" in capsys.readouterr().err


def test_migrate_brings_a_new_database_to_the_current_revision(
    environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["migrate"]) == 0
    assert "0001" in capsys.readouterr().out
    engine = open_engine(environment)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM police_calls")).scalar() == 0
    engine.dispose()
    assert cli.main(["migrate"]) == 0


def test_migrate_refuses_while_the_worker_holds_the_database(
    environment: Path, capsys: pytest.CaptureFixture[str], clock: ManualClock
) -> None:
    holder = Writer(environment, clock=clock)
    holder.lock()
    assert cli.main(["migrate"]) == 1
    assert "already has a writer" in capsys.readouterr().err
    holder.close()


def test_schema_prints_the_committed_contract(
    environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["schema"]) == 0
    assert capsys.readouterr().out == SCHEMA.read_text()


def test_backup_writes_a_copy_and_keeps_the_configured_number(
    environment: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PANEL_BACKUP_KEEP", "2")
    assert cli.main(["migrate"]) == 0
    for _ in range(3):
        assert cli.main(["backup"]) == 0
    directory = environment.parent / "backups"
    assert len(stored_backups(directory)) == 2
    assert stored_backups(directory)[-1].name in capsys.readouterr().out


def test_backup_reports_a_database_that_is_not_there(
    environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["backup"]) == 1
    assert "No database" in capsys.readouterr().err


def test_api_serves_the_application_on_the_requested_address(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: dict[str, Any] = {}

    def record(app: object, **options: Any) -> None:
        served["app"] = app
        served.update(options)

    monkeypatch.setattr(cli.uvicorn, "run", record)
    assert cli.main(["api", "--host", "127.0.0.1", "--port", "9000"]) == 0
    assert (served["host"], served["port"]) == ("127.0.0.1", 9000)
    assert (served["loop"], served["http"], served["ws"]) == ("uvloop", "httptools", "none")
    assert [route.path for route in served["app"].routes if hasattr(route, "path")] >= [
        "/graphql",
        "/events",
        "/healthz",
        "/readyz",
    ]


def test_worker_migrates_then_ingests_with_the_configured_identity(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[Worker] = []

    async def stop_immediately(self: Worker, stop: object = None) -> None:
        started.append(self)
        self.writer.lock()
        self.writer.initialize()

    monkeypatch.setattr(Worker, "run", stop_immediately)
    assert cli.main(["worker"]) == 0

    worker = started[0]
    settings = Settings()
    assert worker.settings.source_url == settings.source_url
    assert worker.client.headers["user-agent"] == settings.user_agent
    assert worker.client.timeout.read == settings.request_timeout_seconds
    engine = open_engine(environment)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM source_generations")).scalar() == 0
    engine.dispose()


def test_worker_reports_another_worker_already_running(
    environment: Path, capsys: pytest.CaptureFixture[str], clock: ManualClock
) -> None:
    holder = Writer(environment, clock=clock)
    holder.lock()
    assert cli.main(["worker"]) == 1
    assert "already has a writer" in capsys.readouterr().err
    holder.close()


def test_the_schema_command_matches_the_served_schema(environment: Path) -> None:
    assert str(build_schema(Settings())) + "\n" == SCHEMA.read_text()


def test_a_stopped_worker_leaves_no_lock_behind(
    environment: Path, monkeypatch: pytest.MonkeyPatch, clock: ManualClock
) -> None:
    async def run_once(self: Worker, stop: object = None) -> None:
        self.writer.lock()
        self.writer.initialize()

    monkeypatch.setattr(Worker, "run", run_once)
    assert cli.main(["worker"]) == 0

    # Another worker can start, so the first released the database.
    after = Writer(environment, clock=clock)
    after.lock()
    after.close()
