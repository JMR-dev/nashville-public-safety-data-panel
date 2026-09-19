"""Compatibility of the published schema with the stored one.

Attributes the source adds are compatible: they are preserved in each record's raw JSON. A known
field that disappears, changes type, or carries values of another type is incompatible, and
ingestion for the source stops until the schema is compatible again.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from panel.payload import as_dict
from panel.tables import SOURCE_FIELDS


@dataclass(frozen=True)
class SchemaCheck:
    compatible: bool
    problems: list[str]
    added: list[str]


def _integer(value: Any) -> bool:
    return type(value) is int


def _number(value: Any) -> bool:
    return type(value) in (int, float)


def _text(value: Any) -> bool:
    return type(value) is str


VALIDATORS = {
    "esriFieldTypeOID": _integer,
    "esriFieldTypeInteger": _integer,
    "esriFieldTypeDate": _integer,
    "esriFieldTypeDouble": _number,
    "esriFieldTypeString": _text,
}


def check_fields(fields: Sequence[Any]) -> SchemaCheck:
    problems: list[str] = []
    published: dict[str, Any] = {}
    for field in fields:
        description = as_dict(field) or {}
        name = description.get("name")
        if not isinstance(name, str):
            problems.append(f"Field description is malformed: {field!r}")
            continue
        published[name] = description.get("type")
    for name, expected in SOURCE_FIELDS.items():
        if name not in published:
            problems.append(f"{name} is missing")
        elif published[name] != expected:
            problems.append(f"{name} changed type from {expected} to {published[name]}")
    added = [name for name in published if name not in SOURCE_FIELDS]
    return SchemaCheck(compatible=not problems, problems=sorted(problems), added=added)


def check_rows(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    for row in rows:
        for name, esri_type in SOURCE_FIELDS.items():
            value = row.get(name)
            if value is not None and not VALIDATORS[esri_type](value):
                problems.append(
                    f"OBJECTID {row.get('OBJECTID')}: {name} has unexpected value {value!r}"
                )
    return problems
