"""Runtime configuration, read from ``PANEL_*`` environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ACTIVE_LAYER = (
    "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/"
    "Metro_Nashville_Police_Department_Calls_for_Service_view/FeatureServer/0"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANEL_")

    database: Path = Path("data/panel.sqlite")

    # Ingestion
    source: str = "mnpd-calls"
    source_url: str = ACTIVE_LAYER
    user_agent: str = (
        "NashvillePublicSafetyPanel/0.1 "
        "(+https://github.com/JMR-dev/nashville-public-safety-data-panel)"
    )
    backfill_concurrency: int = Field(default=32, ge=1, le=32)
    writer_queue: int = Field(default=64, ge=1)
    window_hours: int = Field(default=48, ge=1)
    window_interval_seconds: float = Field(default=300, gt=0)
    nightly_hour: int = Field(default=3, ge=0, le=23)
    degraded_wait_seconds: float = Field(default=60, ge=60)
    request_timeout_seconds: float = Field(default=30, gt=0)

    # API
    max_page_size: int = Field(default=200, ge=1)
    # The dashboard's map selection at this size costs just under max_cost.
    max_map_results: int = Field(default=4000, ge=1)
    max_range_days: int = Field(default=92, ge=1)
    max_depth: int = Field(default=6, ge=1)
    max_aliases: int = Field(default=10, ge=1)
    max_tokens: int = Field(default=2000, ge=1)
    max_cost: int = Field(default=50_000, ge=1)
    event_poll_seconds: float = Field(default=0.25, gt=0)
    event_min_interval_seconds: float = Field(default=1.0, gt=0)

    # Backups
    backup_dir: Path = Path("data/backups")
    backup_keep: int = Field(default=7, ge=1)
