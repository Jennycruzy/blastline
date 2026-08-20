"""Small typed client for the documented HydraDB REST surface.

The graph projection remains locally replayable because HydraDB's public API
accepts typed content and exposes graph-enriched recall, rather than exposing a
raw arbitrary-edge write endpoint. Blastline sends each canonical node/edge
record to HydraDB as an idempotent memory and keeps the same record locally for
exact temporal traversal and offline verification.
"""

from __future__ import annotations

import hashlib
import http.client
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
    context_status_batch_size: int = 50


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
    status_batch_value = section.get("context_status_batch_size", 50)
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
    if isinstance(status_batch_value, bool) or not isinstance(status_batch_value, int) or status_batch_value < 1:
        raise ConfigurationError("hydra context status batch size must be a positive integer")
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
        context_status_batch_size=status_batch_value,
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

    @staticmethod
    def _multipart_body(fields: dict[str, str]) -> tuple[str, bytes]:
        boundary = "----blastline-hydra-" + hashlib.sha256(
            json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                (
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode(),
                    b"\r\n",
                )
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return f"multipart/form-data; boundary={boundary}", b"".join(chunks)

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

    def request(
        self,
        method: str,
        path: str,
        payload: JsonObject | None = None,
        cache: bool = True,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
    ) -> HydraResponse:
        if not self.live_enabled:
            raise ConfigurationError("HYDRA_DB_API_KEY is required for live HydraDB calls")
        key = self._cache_key(method, path, payload)
        cached = self._load_cache(key) if cache else None
        if cached is not None and method.upper() != "GET":
            return cached
        body = raw_body
        if body is None and payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        url = f"{self.config.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "API-Version": "2",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "blastline/0.1.0",
        }
        if cached is not None and cached.etag is not None:
            headers["If-None-Match"] = cached.etag
        last_error: Exception | None = None
        last_detail: str | None = None
        for attempt in range(self.config.retry_attempts):
            request = Request(url=url, data=body, headers=headers, method=method.upper())
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
                last_detail = exc.read().decode("utf-8", errors="replace")
                last_error = exc
            except (
                URLError,
                TimeoutError,
                OSError,
                http.client.IncompleteRead,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                last_error = exc
            if attempt + 1 < self.config.retry_attempts:
                time.sleep(self.config.retry_base_seconds * (2**attempt))
        suffix = f": {last_detail}" if last_detail else ""
        raise ExternalCallError(f"HydraDB {method} {path} failed after retries{suffix}") from last_error

    @staticmethod
    def _data_response(response: HydraResponse, context: str) -> HydraResponse:
        data = response.body.get("data")
        if not isinstance(data, dict):
            raise ExternalCallError(f"HydraDB {context} response did not contain an object data payload")
        return HydraResponse(status=response.status, body=data, from_cache=response.from_cache, etag=response.etag)

    def create_tenant(self, metadata_schema: list[JsonObject]) -> HydraResponse:
        payload: JsonObject = {"database": self.config.tenant_id, "database_metadata_schema": metadata_schema}
        return self.request("POST", "/databases", payload)

    def database_status(self) -> HydraResponse:
        query = urlencode({"database": self.config.tenant_id})
        response = self.request("GET", f"/databases/status?{query}", cache=False)
        return self._data_response(response, "database status")

    def add_memory(self, source_id: str, text: str, metadata: JsonObject) -> HydraResponse:
        return self.add_memories(((source_id, text, metadata),))

    def add_memories(self, records: tuple[tuple[str, str, JsonObject], ...]) -> HydraResponse:
        if not records:
            raise ValueError("HydraDB memory batch cannot be empty")
        app_knowledge: list[JsonObject] = []
        for source_id, text, metadata in records:
            app_knowledge.append(
                {
                    "id": source_id,
                    "database": self.config.tenant_id,
                    "collection": self.config.sub_tenant_id,
                    "title": source_id,
                    "type": "blastline_graph_record",
                    "content": {"text": text},
                    "metadata": {},
                    "additional_metadata": metadata,
                }
            )
        fields = {
            "type": "knowledge",
            "database": self.config.tenant_id,
            "collection": self.config.sub_tenant_id,
            "upsert": "true",
            "app_knowledge": json.dumps(app_knowledge, sort_keys=True, separators=(",", ":")),
        }
        cache_payload: JsonObject = {key: value for key, value in fields.items()}
        content_type, body = self._multipart_body(fields)
        response = self.request("POST", "/context/ingest", cache_payload, raw_body=body, content_type=content_type)
        return self._data_response(response, "context ingest")

    def recall(self, query: str, max_results: int, cache: bool = True) -> HydraResponse:
        payload: JsonObject = {
            "database": self.config.tenant_id,
            "collection": self.config.sub_tenant_id,
            "query": query,
            "type": "knowledge",
            "query_by": "hybrid",
            "max_results": max_results,
            "mode": "thinking",
            "graph_context": True,
            "query_forceful_relations": True,
        }
        response = self.request("POST", "/query", payload, cache=cache)
        return self._data_response(response, "query")

    def graph_relations_by_source_id(self, source_id: str | None = None, cache: bool = True) -> HydraResponse:
        """Return HydraDB's structured relation triplets for one stored source.

        This endpoint is intentionally separate from recall. Recall discovers
        candidate context; this call inspects the hosted graph relation data
        attached to a concrete source ID. Callers must still validate the
        returned records against Blastline's typed temporal graph before making
        a security claim.
        """

        params = {
            "database": self.config.tenant_id,
            "collection": self.config.sub_tenant_id,
            "limit": "500",
        }
        if source_id is not None:
            params["id"] = source_id
        query = urlencode(params)
        response = self.request("GET", f"/context/relations?{query}", cache=cache)
        return self._data_response(response, "context relations")

    def context_status(self, source_ids: tuple[str, ...]) -> HydraResponse:
        if not source_ids:
            raise ValueError("HydraDB context status requires at least one source ID")
        query = urlencode(
            {
                "database": self.config.tenant_id,
                "collection": self.config.sub_tenant_id,
                "ids": ",".join(source_ids),
            }
        )
        response = self.request("GET", f"/context/status?{query}", cache=False)
        return self._data_response(response, "context status")

    def wait_for_sources(self, source_ids: tuple[str, ...], attempts: int, delay_seconds: float) -> None:
        if not source_ids:
            raise ValueError("HydraDB context wait requires at least one source ID")
        if attempts < 1 or delay_seconds < 0:
            raise ValueError("HydraDB context wait configuration is invalid")
        batch_size = self.config.context_status_batch_size
        if batch_size < 1:
            raise ValueError("HydraDB context status batch size must be positive")
        status_batches = tuple(
            source_ids[start : start + batch_size]
            for start in range(0, len(source_ids), batch_size)
        )
        terminal = {"completed"}
        last_status_error: ExternalCallError | None = None
        for attempt in range(attempts):
            try:
                statuses: list[str] = []
                for status_batch in status_batches:
                    response = self.context_status(status_batch)
                    raw_statuses = response.body.get("statuses")
                    if not isinstance(raw_statuses, list):
                        raise ExternalCallError("HydraDB context status did not return statuses")
                    if len(raw_statuses) != len(status_batch):
                        raise ExternalCallError("HydraDB context status omitted one or more submitted source IDs")
                    for index, raw_status in enumerate(raw_statuses):
                        status = require_object(raw_status, f"Hydra context status[{index}]")
                        state = require_string(
                            status.get("indexing_status"),
                            f"Hydra context status[{index}].indexing_status",
                        )
                        statuses.append(state)
                        if state == "errored":
                            error_code = status.get("error_code")
                            error_message = status.get("error_message")
                            raise ExternalCallError(
                                f"HydraDB failed to process source {status.get('id')}: "
                                f"{error_code if isinstance(error_code, str) else 'unknown'} "
                                f"{error_message if isinstance(error_message, str) else ''}".strip()
                            )
            except ExternalCallError as exc:
                # The ingest endpoint can accept a batch before the status
                # endpoint is responsive. Keep polling instead of converting
                # one transient readiness failure into a failed publication.
                last_status_error = exc
                if attempt + 1 < attempts:
                    time.sleep(delay_seconds)
                    continue
                break
            last_status_error = None
            if all(state in terminal for state in statuses):
                return
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
        if last_status_error is not None:
            raise ExternalCallError(
                "HydraDB context status remained unavailable before the configured wait limit"
            ) from last_status_error
        raise ExternalCallError("HydraDB context did not reach completed graph state before the configured wait limit")

    def list_source(self, source_id: str) -> HydraResponse:
        payload: JsonObject = {
            "database": self.config.tenant_id,
            "collection": self.config.sub_tenant_id,
            "type": "knowledge",
            "page": 1,
            "page_size": 1,
            "ids": [source_id],
        }
        response = self.request("POST", "/context/list", payload)
        return self._data_response(response, "context list")

    def list_sources(self, page: int, page_size: int, cache: bool = False) -> HydraResponse:
        if page < 1 or page_size < 1:
            raise ValueError("HydraDB context list pagination must be positive")
        payload: JsonObject = {
            "database": self.config.tenant_id,
            "collection": self.config.sub_tenant_id,
            "type": "knowledge",
            "page": page,
            "page_size": page_size,
        }
        response = self.request("POST", "/context/list", payload, cache=cache)
        return self._data_response(response, "context list")


def response_success(response: HydraResponse) -> bool:
    value = response.body.get("success")
    if isinstance(value, bool):
        return value
    nested = response.body.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("success"), bool):
        return require_bool(nested.get("success"), "Hydra response data.success")
    raise ValueError("Hydra response success must be a boolean")
