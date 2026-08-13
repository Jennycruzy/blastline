"""Cached, retrying HTTP for public source APIs."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..errors import ExternalCallError
from ..json_types import JsonObject, require_int, require_object, require_string


@dataclass(frozen=True, slots=True)
class HttpPolicy:
    timeout_seconds: int
    retry_attempts: int
    retry_base_seconds: float


@dataclass(frozen=True, slots=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    from_cache: bool


class DiskHttpClient:
    def __init__(self, cache_directory: Path, policy: HttpPolicy, user_agent: str) -> None:
        self.cache_directory = cache_directory
        self.policy = policy
        self.user_agent = user_agent
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    def _key(self, method: str, url: str, body: bytes | None) -> str:
        material = method.upper().encode() + b"\0" + url.encode() + b"\0" + (body if body is not None else b"")
        return hashlib.sha256(material).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_directory / f"{key}.json"

    def _read(self, path: Path) -> HttpResponse:
        try:
            envelope = require_object(json.loads(path.read_text(encoding="utf-8")), f"HTTP cache {path}")
            url = require_string(envelope.get("url"), f"HTTP cache {path}.url")
            status = require_int(envelope.get("status"), f"HTTP cache {path}.status")
            headers_value = envelope.get("headers")
            body_value = require_string(envelope.get("body_base64"), f"HTTP cache {path}.body_base64")
            if not isinstance(headers_value, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in headers_value.items()):
                raise ValueError(f"HTTP cache {path}.headers must map strings to strings")
            body = base64.b64decode(body_value.encode(), validate=True)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ExternalCallError(f"HTTP cache is corrupt: {path}") from exc
        return HttpResponse(url=url, status=status, headers=dict(headers_value), body=body, from_cache=True)

    def _write(self, path: Path, response: HttpResponse) -> None:
        envelope: JsonObject = {
            "url": response.url,
            "status": response.status,
            "headers": response.headers,
            "body_base64": base64.b64encode(response.body).decode("ascii"),
        }
        path.write_text(json.dumps(envelope, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def fetch(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        refresh: bool = False,
    ) -> HttpResponse:
        normalized_method = method.upper()
        cache_path = self._path(self._key(normalized_method, url, body))
        cached = self._read(cache_path) if cache_path.exists() else None
        if cached is not None and not refresh:
            return cached
        headers = {"Accept": "application/json, text/plain, */*", "User-Agent": self.user_agent}
        if cached is not None:
            etag = cached.headers.get("etag")
            if etag is not None:
                headers["If-None-Match"] = etag
        last_error: Exception | None = None
        for attempt in range(self.policy.retry_attempts):
            request = Request(url=url, data=body, headers=headers, method=normalized_method)
            try:
                with urlopen(request, timeout=self.policy.timeout_seconds) as response:
                    result = HttpResponse(
                        url=url,
                        status=response.status,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=response.read(),
                        from_cache=False,
                    )
                    self._write(cache_path, result)
                    return result
            except HTTPError as exc:
                if exc.code == 304 and cached is not None:
                    return cached
                if exc.code not in (429, 500, 502, 503, 504):
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise ExternalCallError(f"HTTP {normalized_method} {url} failed with {exc.code}: {detail}") from exc
                last_error = exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt + 1 < self.policy.retry_attempts:
                time.sleep(self.policy.retry_base_seconds * (2**attempt))
        raise ExternalCallError(f"HTTP {normalized_method} {url} failed after retries") from last_error
