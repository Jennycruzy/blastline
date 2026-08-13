"""Registry transport and checkpoint handling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from ..errors import ExternalCallError
from ..json_types import JsonObject, JsonValue, require_int, require_object, require_string
from .http import DiskHttpClient, HttpResponse
from .parsers import parse_json_object


@dataclass(frozen=True, slots=True)
class NpmChanges:
    results: tuple[JsonObject, ...]
    last_seq: str


class NpmRegistry:
    def __init__(self, http: DiskHttpClient, registry_url: str, changes_url: str) -> None:
        self.http = http
        self.registry_url = registry_url.rstrip("/")
        self.changes_url = changes_url.rstrip("/")

    def package(self, name: str, refresh: bool = False) -> HttpResponse:
        encoded_name = quote(name, safe="")
        return self.http.fetch(f"{self.registry_url}/{encoded_name}", refresh=refresh)

    def changes(self, since: str, limit: int, refresh: bool = False) -> NpmChanges:
        query = urlencode({"include_docs": "true", "feed": "normal", "since": since, "limit": str(limit)})
        response = self.http.fetch(f"{self.changes_url}/_changes?{query}", refresh=refresh)
        document = parse_json_object(response.body, "npm changes feed")
        results_value = document.get("results")
        last_seq_value = document.get("last_seq")
        if not isinstance(results_value, list):
            raise ValueError("npm changes feed results must be an array")
        if not isinstance(last_seq_value, (str, int)) or isinstance(last_seq_value, bool):
            raise ValueError("npm changes feed last_seq must be a string or integer")
        results: list[JsonObject] = []
        for index, result in enumerate(results_value):
            results.append(require_object(result, f"npm changes result {index}"))
        return NpmChanges(tuple(results), str(last_seq_value))


class PyPIRegistry:
    def __init__(self, http: DiskHttpClient, package_url: str, simple_url: str) -> None:
        self.http = http
        self.package_url = package_url.rstrip("/")
        self.simple_url = simple_url.rstrip("/")

    def package(self, name: str, refresh: bool = False) -> HttpResponse:
        normalized = name.strip()
        if not normalized:
            raise ValueError("PyPI package name cannot be empty")
        encoded_name = quote(normalized, safe="")
        return self.http.fetch(f"{self.package_url}/{encoded_name}/json", refresh=refresh)

    def simple_index(self, refresh: bool = False) -> HttpResponse:
        return self.http.fetch(self.simple_url, refresh=refresh)


def read_checkpoint(path: Path, source: str, initial: str) -> str:
    if not path.exists():
        return initial
    try:
        document = require_object(json.loads(path.read_text(encoding="utf-8")), f"{source} checkpoint")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ExternalCallError(f"{source} checkpoint cannot be read: {path}") from exc
    value = document.get("since")
    if not isinstance(value, str):
        raise ExternalCallError(f"{source} checkpoint has no string since value: {path}")
    return value


def write_checkpoint(path: Path, source: str, since: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {"source": source, "since": since}
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
