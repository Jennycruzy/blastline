"""Small standard-library server for the live temporal exposure timeline."""

from __future__ import annotations

import argparse
import json
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from blastline.config import Settings
from blastline.query.engine import QueryEngine
from blastline.timeutil import format_time, parse_time


class TimelineHandler(BaseHTTPRequestHandler):
    settings: Settings
    engine: QueryEngine
    html_path: Path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.html_path.read_bytes())
            return
        if parsed.path == "/api/timeline":
            self._timeline(parse_qs(parsed.query))
            return
        self._send(404, "text/plain; charset=utf-8", b"not found\n")

    def _timeline(self, query: dict[str, list[str]]) -> None:
        timeline = self.settings.section("timeline")
        registry = self._one(query, "registry", self._config_string(timeline, "demo_registry"))
        package = self._one(query, "package", self._config_string(timeline, "demo_package"))
        version = self._one(query, "version", self._config_string(timeline, "demo_version"))
        start = parse_time(self._one(query, "from", self._config_string(timeline, "demo_from")), "timeline from")
        end = parse_time(self._one(query, "to", self._config_string(timeline, "demo_to")), "timeline to")
        if end <= start:
            self._send_json(400, {"error": "timeline to must be after from"})
            return
        frame_count = self.settings.integer("timeline", "frame_count")
        if frame_count < 1:
            self._send_json(500, {"error": "timeline.frame_count must be positive"})
            return
        duration = (end - start) / frame_count
        frames: list[dict[str, object]] = []
        for index in range(frame_count):
            frame_end = end if index == frame_count - 1 else start + duration * (index + 1)
            began = time.perf_counter()
            response = self.engine.window_exposure(registry, package, version, (start, frame_end))
            latency_ms = (time.perf_counter() - began) * 1000
            frames.append(
                {
                    "frame": index,
                    "from": format_time(start),
                    "to": format_time(frame_end),
                    "latency_ms": round(latency_ms, 3),
                    "exposed_repositories": sorted(
                        str(item["repository"])
                        for item in response.results
                        if isinstance(item.get("repository"), str)
                    ),
                    "query": response.as_json(),
                }
            )
        self._send_json(
            200,
            {
                "mode": "live-temporal-query",
                "registry": registry,
                "package": package,
                "version": version,
                "frames": frames,
            },
        )

    @staticmethod
    def _one(query: dict[str, list[str]], key: str, default: str) -> str:
        values = query.get(key)
        if not values or not values[0].strip():
            return default
        return values[0]

    @staticmethod
    def _config_string(section: dict[str, object], key: str) -> str:
        value = section.get(key)
        if not isinstance(value, str):
            raise ValueError(f"timeline.{key} must be a string")
        return value

    def _send_json(self, status: int, value: dict[str, object]) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(value, sort_keys=True).encode())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def serve(root: Path, host: str, port: int) -> None:
    settings = Settings.load(root)
    handler = TimelineHandler
    handler.settings = settings
    from blastline.store import GraphStore

    handler.engine = QueryEngine(GraphStore(settings.path("graph", "directory")), settings)
    handler.html_path = root / "ui" / "index.html"
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Blastline timeline: http://{host}:{port}/")
    print("Every frame is a live Q3 temporal query; stop with Ctrl-C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("timeline stopped")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="blastline-timeline")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(Path(__file__).resolve().parents[1], args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
