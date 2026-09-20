"""Test helpers: deterministic time and upstream-shaped records."""

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = "mnpd-calls"
URL = "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/example/FeatureServer/0"


class ManualClock:
    """Epoch milliseconds that only move when a test moves them."""

    def __init__(self, now: int = 1_789_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, milliseconds: int) -> None:
        self.now += milliseconds


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def call_row(oid: int, **overrides: Any) -> dict[str, Any]:
    """A record shaped exactly like the published layer's attributes."""
    row: dict[str, Any] = {
        "OBJECTID": oid,
        "Event_Number": f"PD2026{oid:08d}",
        "Complaint_Number": None,
        "Tencode": 70,
        "Tencode_Description": "ALARM - BURGLAR",
        "Tencode_Suffix": None,
        "Tencode_Suffix_Description": None,
        "Disposition_Code": "4",
        "Disposition_Description": "ASSISTED CITIZEN",
        "Block": "100",
        "Street_Name": "BROADWAY",
        "Unit_Dispatched": "121B",
        "Shift": "B",
        "Sector": "C1",
        "Mapped_Location": None,
        "POINT_X": -9667000.5,
        "POINT_Y": 4323000.25,
        "ZONE_": "15",
        "Latitude": 36.16,
        "Longitude": -86.78,
        "RPA": "2201",
        "Call_Received": 1_788_990_000_000 + oid * 1000,
    }
    row.update(overrides)
    return row
