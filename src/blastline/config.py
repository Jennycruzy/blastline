"""Configuration loading with explicit failure for missing values."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError
from .json_types import JsonObject, JsonValue, require_bool, require_int, require_object, require_string


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    values: JsonObject

    @classmethod
    def load(cls, root: Path) -> "Settings":
        path = root / "config" / "default.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"cannot read configuration: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"configuration is invalid JSON: {path}") from exc
        return cls(root=root, values=require_object(raw, "configuration"))

    def section(self, name: str) -> JsonObject:
        value = self.values.get(name)
        return require_object(value, f"configuration section {name!r}")

    def string(self, section: str, key: str) -> str:
        return require_string(self.section(section).get(key), f"configuration {section}.{key}")

    def integer(self, section: str, key: str) -> int:
        return require_int(self.section(section).get(key), f"configuration {section}.{key}")

    def boolean(self, section: str, key: str) -> bool:
        return require_bool(self.section(section).get(key), f"configuration {section}.{key}")

    def number(self, section: str, key: str) -> float:
        value: JsonValue = self.section(section).get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigurationError(f"configuration {section}.{key} must be numeric")
        return float(value)

    def path(self, section: str, key: str) -> Path:
        configured = self.string(section, key)
        path = Path(configured)
        if not path.is_absolute():
            path = self.root / path
        return path
