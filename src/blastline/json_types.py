"""Narrow JSON types used at every external boundary."""

from __future__ import annotations

from typing import TypeAlias

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def require_object(value: JsonValue, context: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{context} contains a non-string key")
    return value


def require_string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    return value


def require_bool(value: JsonValue, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def require_int(value: JsonValue, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} must be an integer")
    return value


def optional_string(value: JsonValue | None, context: str) -> str | None:
    if value is None:
        return None
    return require_string(value, context)
