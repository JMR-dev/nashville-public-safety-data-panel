"""Deployment configuration comes from PANEL_* environment variables."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from panel.settings import ACTIVE_LAYER, Settings


def test_defaults_follow_the_agreed_ingestion_and_api_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PANEL_DATABASE", "PANEL_SOURCE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.source_url == ACTIVE_LAYER
    assert settings.database == Path("data/panel.sqlite")
    assert (settings.backfill_concurrency, settings.window_hours) == (32, 48)
    assert settings.window_interval_seconds == 300
    assert settings.degraded_wait_seconds == 60
    assert (settings.max_page_size, settings.max_map_results, settings.max_range_days) == (
        200,
        4000,
        92,
    )
    assert settings.backup_keep == 7


def test_environment_overrides_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANEL_DATABASE", "/var/lib/panel/panel.sqlite")
    monkeypatch.setenv("PANEL_MAX_PAGE_SIZE", "100")
    settings = Settings()
    assert settings.database == Path("/var/lib/panel/panel.sqlite")
    assert settings.max_page_size == 100


def test_unsafe_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANEL_DEGRADED_WAIT_SECONDS", "5")
    with pytest.raises(ValidationError, match="degraded_wait_seconds"):
        Settings()
