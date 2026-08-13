"""Small typed client for the documented HydraDB REST surface.

The graph projection remains locally replayable because HydraDB's public API
accepts typed content and exposes graph-enriched recall, rather than exposing a
raw arbitrary-edge write endpoint. Blastline sends each canonical node/edge
record to HydraDB as an idempotent memory and keeps the same record locally for
exact temporal traversal and offline verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import ConfigurationError, ExternalCallError
from .json_types import JsonObject, JsonValue, require_bool, require_int, require_object, require_string


@dataclass(frozen=True, slots=True)
class HydraConfig:
    base_url: str
    tenant_id: str
    sub_tenant_id: str
    timeout_seconds: int
    retry_attempts: int
    retry_base_seconds: float
    cache_directory: Path


@dataclass(frozen=True, slots=True)
class HydraResponse:
    status: int
    body: JsonObject
    from_cache: bool
    etag: str | None


def load_hydra_config(root: Path, values: JsonObject) -> HydraConfig:
    section = values.get("hydra")
    if not isinstance(section, dict):
        raise ConfigurationError("configuration hydra section is missing")
    base_url_value = section.get("base_url")
    tenant_value = os.environ.get("HYDRA_DB_TENANT_ID", section.get("tenant_id"))
    subtenant_value = os.environ.get("HYDRA_DB_SUB_TENANT_ID", section.get("sub_tenant_id"))
    timeout_value = section.get("request_timeout_seconds")
    attempts_value = section.get("retry_attempts")
    base_retry_value = section.get("retry_base_seconds")
    cache_value = section.get("cache_directory")
    if not isinstance(base_url_value, str) or not isinstance(tenant_value, str) or not isinstance(subtenant_value, str):
        raise ConfigurationError("hydra URL and tenant identifiers must be strings")
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, int):
        raise ConfigurationError("hydra request timeout must be an integer")
    if isinstance(attempts_value, bool) or not isinstance(attempts_value, int):
        raise ConfigurationError("hydra retry attempts must be an integer")
    if isinstance(base_retry_value, bool) or not isinstance(base_retry_value, (int, float)):
        raise ConfigurationError("hydra retry base must be numeric")
    if not isinstance(cache_value, str):
        raise ConfigurationError("hydra cache directory must be a string")
    cache_path = Path(cache_value)
    if not cache_path.is_absolute():
        cache_path = root / cache_path
    return HydraConfig(
        base_url=base_url_value.rstrip("/"),
        tenant_id=tenant_value,
        sub_tenant_id=subtenant_value,
        timeout_seconds=timeout_value,
        retry_attempts=attempts_value,
        retry_base_seconds=float(base_retry_value),
        cache_directory=cache_path,
    )


class HydraClient:
    def __init__(self, config: HydraConfig, token: str | None = None) -> None:
        self.config = config
        self.token = token if token is not None else os.environ.get("HYDRA_DB_API_KEY")
        self.config.cache_directory.mkdir(parents=True, exist_ok=True)

    @property
    def live_enabled(self) -> bool:
        return self.token is not None and bool(self.token.strip())

    def _cache_key(self, method: str, path: str, payload: JsonObject | None) -> str:
        material = json.dumps(
            {"method": method, "path": path, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(material).hexdigest()

    def _cache_paths(self, key: str) -> tuple[Path, Path]:
        return self.config.cache_directory / f"{key}.json", self.config.cache_directory / f"{key}.meta.json"

    def _load_cache(self, key: str) -> HydraResponse | None:
        body_path, meta_path = self._cache_paths(key)
        if not body_path.exists() or not meta_path.exists():
            return None
        try:
            body = require_object(json.loads(body_path.read_text(encoding="utf-8")), f"Hydra cache {body_path}")
            meta = require_object(json.loads(meta_path.read_text(encoding="utf-8")), f"Hydra cache {meta_path}")
            status = require_int(meta.get("status"), f"Hydra cache {meta_path}.status")
            etag_value = meta.get("etag")
            etag = etag_value if etag_value is None or isinstance(etag_value, str) else None
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ExternalCallError(f"Hydra cache is corrupt for request {key}") from exc
        return HydraResponse(status=status, body=body, from_cache=True, etag=etag)

    def _save_cache(self, key: str, response: HydraResponse) -> None:
        body_path, meta_path = self._cache_paths(key)
        body_path.write_text(json.dumps(response.body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        meta: JsonObject = {"status": response.status}
        if response.etag is not None:
            meta["etag"] = response.etag
        meta_path.write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def request(self, method: str, path: str, payload: JsonObject | None = None, cache: bool = True) -> HydraResponse:
        if not self.live_enabled:
            raise ConfigurationError("HYDRA_DB_API_KEY is required for live HydraDB calls")
        key = self._cache_key(method, path, payload)
        cached = self._load_cache(key) if cache else None
        if cached is not None and method.upper() != "GET":
            return cached
        query: str = ""
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        url = f"{self.config.base_url}{path}"
        if "?" in path:
            url = f"{self.config.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "blastline/0.1.0",
        }
        if cached is not None and cached.etag is not None:
            headers["If-None-Match"] = cached.etag
        last_error: Exception | None = None
        for attempt in range(self.config.retry_attempts):
            request = Request(url=f"{url}{query}", data=body, headers=headers, method=method.upper())
            try:
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    raw = response.read()
                    parsed = require_object(json.loads(raw), f"Hydra response {path}")
                    etag = response.headers.get("ETag")
                    result = HydraResponse(status=response.status, body=parsed, from_cache=False, etag=etag)
                    if cache:
                        self._save_cache(key, result)
                    return result
            except HTTPError as exc:
                if exc.code == 304 and cached is not None:
                    return cached
                if exc.code not in (429, 500, 502, 503, 504):
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise ExternalCallError(f"HydraDB {method} {path} failed with HTTP {exc.code}: {detail}") from exc
                last_error = exc
            except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
            if attempt + 1 < self.config.retry_attempts:
                time.sleep(self.config.retry_base_seconds * (2**attempt))
        raise ExternalCallError(f"HydraDB {method} {path} failed after retries") from last_error

    def create_tenant(self, metadata_schema: list[JsonObject]) -> HydraResponse:
        payload: JsonObject = {"tenant_id": self.config.tenant_id, "tenant_metadata_schema": metadata_schema}
        return self.request("POST", "/tenants/create", payload)

    def add_memory(self, source_id: str, text: str, metadata: JsonObject) -> HydraResponse:
        memory: JsonObject = {
            "source_id": source_id,
            "text": text,
            "is_markdown": True,
            "infer": False,
            "title": source_id,
            "additional_metadata": metadata,
        }
        payload: JsonObject = {
            "tenant_id": self.config.tenant_id,
            "sub_tenant_id": self.config.sub_tenant_id,
            "memories": [memory],
            "upsert": True,
        }
        return self.request("POST", "/memories/add_memory", payload)

    def recall(self, query: str, max_results: int) -> HydraResponse:
        payload: JsonObject = {
            "tenant_id": self.config.tenant_id,
            "sub_tenant_id": self.config.sub_tenant_id,
            "query": query,
            "max_results": max_results,
            "mode": "fast",
            "alpha": 0.0,
            "graph_context": True,
            "search_forceful_relations": True,
        }
        return self.request("POST", "/recall/full_recall", payload)

    def list_source(self, source_id: str) -> HydraResponse:
        payload: JsonObject = {
            "tenant_id": self.config.tenant_id,
            "sub_tenant_id": self.config.sub_tenant_id,
            "kind": "knowledge",
            "page": 1,
            "page_size": 1,
            "source_ids": [source_id],
            "include_fields": ["title", "content", "additional_metadata"],
        }
        return self.request("POST", "/list/data", payload)


def response_success(response: HydraResponse) -> bool:
    value = response.body.get("success")
    return require_bool(value, "Hydra response success")
