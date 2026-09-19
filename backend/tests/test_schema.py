"""Upstream schema drift: additions are kept, incompatible changes stop ingestion."""

from typing import Any

import pytest

from panel.schema import check_fields, check_rows
from tests.support import call_row, load_fixture


def published_fields() -> list[dict[str, Any]]:
    return load_fixture("layer_metadata.json")["fields"]


def test_verified_layer_schema_is_compatible() -> None:
    result = check_fields(published_fields())
    assert (result.compatible, result.problems, result.added) == (True, [], [])


def test_added_fields_are_compatible_and_reported() -> None:
    fields = [*published_fields(), {"name": "Priority", "type": "esriFieldTypeString"}]
    result = check_fields(fields)
    assert (result.compatible, result.added) == (True, ["Priority"])


def test_missing_or_retyped_fields_are_incompatible() -> None:
    fields = [field for field in published_fields() if field["name"] != "ZONE_"]
    for field in fields:
        if field["name"] == "Call_Received":
            field["type"] = "esriFieldTypeString"
    result = check_fields(fields)
    assert not result.compatible
    assert result.problems == [
        "Call_Received changed type from esriFieldTypeDate to esriFieldTypeString",
        "ZONE_ is missing",
    ]


def test_malformed_field_descriptions_are_incompatible() -> None:
    result = check_fields([*published_fields(), "Priority", {"type": "esriFieldTypeString"}])
    assert not result.compatible
    assert result.problems == ["Field description is malformed: 'Priority'"] + [
        "Field description is malformed: {'type': 'esriFieldTypeString'}"
    ]


def test_published_sample_rows_match_the_schema() -> None:
    rows = [feature["attributes"] for feature in load_fixture("query_sample.json")["features"]]
    assert check_rows(rows) == []


def test_whole_numbers_are_valid_doubles_and_extra_attributes_are_allowed() -> None:
    assert check_rows([call_row(1, POINT_X=-9667000, Priority="HIGH")]) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Call_Received", "2026-09-19T12:00:00Z"),
        ("Tencode", True),
        ("Tencode", 7.5),
        ("Latitude", "36.1"),
        ("Event_Number", 12),
    ],
)
def test_values_of_the_wrong_type_are_incompatible(field: str, value: Any) -> None:
    problems = check_rows([call_row(1), call_row(2, **{field: value})])
    assert problems == [f"OBJECTID 2: {field} has unexpected value {value!r}"]
