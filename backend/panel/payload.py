"""Narrow decoded JSON values to the container types the code expects."""

from typing import Any, cast


def as_dict(value: object) -> dict[str, Any] | None:
    """The value as a JSON object, or None. JSON object keys are always strings."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def as_list(value: object) -> list[Any] | None:
    return cast(list[Any], value) if isinstance(value, list) else None
