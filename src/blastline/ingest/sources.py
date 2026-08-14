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


@dataclass(frozen=True, slots=True)
class NpmCatalogPage:
    results: tuple[JsonObject, ...]
    next_key: str | None
    next_docid: str | None
    exhausted: bool


class NpmRegistry:
    def __init__(self, http: DiskHttpClient, registry_url: str, changes_url: str) -> None:
        self.http = http
        self.registry_url = registry_url.rstrip("/")
        self.changes_url = changes_url.rstrip("/")

    def package(self, name: str, refresh: bool = False) -> HttpResponse:
        encoded_name = quote(name, safe="")
        return self.http.fetch(f"{self.registry_url}/{encoded_name}", refresh=refresh)

    def all_docs(self, startkey: str | None, startkey_docid: str | None, limit: int, refresh: bool = False) -> NpmCatalogPage:
        if limit < 1:
            raise ValueError("npm catalog page limit must be positive")
        parameters: dict[str, str] = {"limit": str(limit)}
        if startkey is not None:
            parameters["startkey"] = json.dumps(startkey, separators=(",", ":"))
        if startkey_docid is not None:
            parameters["startkey_docid"] = startkey_docid
        query = urlencode(parameters)
        response = self.http.fetch(
            f"{self.changes_url}/_all_docs?{query}",
            refresh=refresh,
            extra_headers={"npm-replication-opt-in": "true"},
        )
        document = parse_json_object(response.body, "npm catalog response")
        rows_value = document.get("rows")
        if not isinstance(rows_value, list):
            raise ValueError("npm catalog response rows must be an array")
        rows: list[JsonObject] = []
        for index, row_value in enumerate(rows_value):
            row = require_object(row_value, f"npm catalog row {index}")
            identifier = row.get("id")
            if not isinstance(identifier, str):
                raise ValueError(f"npm catalog row {index}.id must be a string")
            rows.append(row)
        if not rows:
            return NpmCatalogPage((), None, None, True)
        last = rows[-1]
        last_id = last.get("id")
        last_key = last.get("key")
        if not isinstance(last_id, str):
            raise ValueError("npm catalog last row id must be a string")
        if not isinstance(last_key, str):
            last_key = last_id
        return NpmCatalogPage(tuple(rows), last_key, last_id, len(rows) < limit)

    def changes(self, since: str, limit: int, refresh: bool = False) -> NpmChanges:
        query = urlencode({"since": since, "limit": str(limit)})
        response = self.http.fetch(
            f"{self.changes_url}/_changes?{query}",
            refresh=refresh,
            extra_headers={"npm-replication-opt-in": "true"},
        )
        document = parse_json_object(response.body, "npm changes feed")
        results_value = document.get("results")
        last_seq_value = document.get("last_seq")
        if not isinstance(results_value, list):
            raise ValueError("npm changes feed results must be an array")
        if not isinstance(last_seq_value, (str, int)) or isinstance(last_seq_value, bool):
            raise ValueError("npm changes feed last_seq must be a string or integer")
        results: list[JsonObject] = []
        for index, result in enumerate(results_value):
            change = require_object(result, f"npm changes result {index}")
            identifier = change.get("id")
            if not isinstance(identifier, str):
                raise ValueError(f"npm changes result {index}.id must be a string")
            if isinstance(change.get("doc"), dict):
                results.append(change)
                continue
            lightweight: JsonObject = {"id": identifier}
            if change.get("deleted") is True:
                lightweight["deleted"] = True
            results.append(lightweight)
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


def read_index_checkpoint(path: Path, source: str, fingerprint: str, total: int) -> int:
    if not path.exists():
        return 0
    try:
        document = require_object(json.loads(path.read_text(encoding="utf-8")), f"{source} checkpoint")
        stored_fingerprint = require_string(document.get("index_fingerprint"), f"{source} checkpoint.index_fingerprint")
        next_index = require_int(document.get("next_index"), f"{source} checkpoint.next_index")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ExternalCallError(f"{source} checkpoint cannot be read: {path}") from exc
    if stored_fingerprint != fingerprint:
        return 0
    if next_index < 0 or next_index > total:
        raise ExternalCallError(f"{source} checkpoint next_index is outside the current catalog: {path}")
    return next_index


def write_index_checkpoint(path: Path, source: str, fingerprint: str, next_index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {
        "source": source,
        "index_fingerprint": fingerprint,
        "next_index": next_index,
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def read_cursor_checkpoint(path: Path, source: str) -> tuple[str | None, str | None, bool]:
    if not path.exists():
        return None, None, False
    try:
        document = require_object(json.loads(path.read_text(encoding="utf-8")), f"{source} checkpoint")
        key_value = document.get("next_key")
        docid_value = document.get("next_docid")
        complete_value = document.get("complete")
        if key_value is not None and not isinstance(key_value, str):
            raise ValueError("next_key must be a string or null")
        if docid_value is not None and not isinstance(docid_value, str):
            raise ValueError("next_docid must be a string or null")
        if not isinstance(complete_value, bool):
            raise ValueError("complete must be a boolean")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ExternalCallError(f"{source} checkpoint cannot be read: {path}") from exc
    return key_value, docid_value, complete_value


def write_cursor_checkpoint(path: Path, source: str, next_key: str | None, next_docid: str | None, complete: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {
        "source": source,
        "next_key": next_key,
        "next_docid": next_docid,
        "complete": complete,
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
