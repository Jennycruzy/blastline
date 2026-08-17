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
from blastline.errors import Abstention
from blastline.hydra import HydraClient, load_hydra_config
from blastline.json_types import JsonObject
from blastline.query.engine import QueryEngine
from blastline.query.hydra_evidence import HydraWindowVerifier
from blastline.timeutil import format_time, parse_time


def build_timeline_payload(settings: Settings, engine: QueryEngine, query: dict[str, list[str]] | None = None) -> JsonObject:
    """Build the live frame payload used by both HTTP and offline checks."""

    values = query if query is not None else {}
    timeline = settings.section("timeline")
    registry = TimelineHandler._one(values, "registry", TimelineHandler._config_string(timeline, "demo_registry"))
    package = TimelineHandler._one(values, "package", TimelineHandler._config_string(timeline, "demo_package"))
    version = TimelineHandler._one(values, "version", TimelineHandler._config_string(timeline, "demo_version"))
    start = parse_time(TimelineHandler._one(values, "from", TimelineHandler._config_string(timeline, "demo_from")), "timeline from")
    end = parse_time(TimelineHandler._one(values, "to", TimelineHandler._config_string(timeline, "demo_to")), "timeline to")
    if end <= start:
        raise ValueError("timeline to must be after from")
    frame_count = settings.integer("timeline", "frame_count")
    if frame_count < 1:
        raise ValueError("timeline.frame_count must be positive")
    duration = (end - start) / frame_count
    frames: list[JsonObject] = []
    hydra = HydraClient(load_hydra_config(settings.root, settings.values))
    hydra_enabled = hydra.live_enabled
    hydra_runner = HydraWindowVerifier(
        hydra,
        engine.store,
        engine,
        settings.integer("hydra", "candidate_result_limit"),
    ) if hydra_enabled else None
    present_response = engine.current_exposure(registry, package, version)
    present_repositories = sorted({
        str(item["repository"])
        for item in present_response.results
        if isinstance(item.get("repository"), str)
    })
    for index in range(frame_count):
        frame_end = end if index == frame_count - 1 else start + duration * (index + 1)
        if hydra_runner is not None:
            try:
                hydra_result = hydra_runner.run(registry, package, version, (start, frame_end), frame_end)
                exposed_repositories = sorted({
                    str(item["repository"])
                    for item in hydra_result.accepted_results
                    if isinstance(item.get("repository"), str)
                })
                frames.append(
                    {
                        "frame": index,
                        "from": format_time(start),
                        "to": format_time(frame_end),
                        "latency_ms": round(hydra_result.latency_ms, 3),
                        "exposed_repositories": exposed_repositories,
                        "hydra_candidate_paths": len(hydra_result.candidate_paths),
                        "hydra_accepted_paths": len(hydra_result.accepted_results),
                        "hydra_rejected_sources": len(hydra_result.rejected_source_ids),
                        "abstentions": list(hydra_result.abstentions),
                        "historical_differs_current": set(exposed_repositories) != set(present_repositories),
                        "query": hydra_result.as_json(),
                    }
                )
            except Abstention as exc:
                frames.append(
                    {
                        "frame": index,
                        "from": format_time(start),
                        "to": format_time(frame_end),
                        "latency_ms": 0.0,
                        "exposed_repositories": [],
                        "hydra_candidate_paths": 0,
                        "hydra_accepted_paths": 0,
                        "hydra_rejected_sources": 0,
                        "abstentions": [str(exc)],
                        "historical_differs_current": False,
                        "query": {"abstained": True, "reason": str(exc)},
                    }
                )
            continue
        began = time.perf_counter()
        response = engine.window_exposure(registry, package, version, (start, frame_end), frame_end)
        latency_ms = (time.perf_counter() - began) * 1000
        exposed_repositories = sorted({
            str(item["repository"])
            for item in response.results
            if isinstance(item.get("repository"), str)
        })
        frames.append(
            {
                "frame": index,
                "from": format_time(start),
                "to": format_time(frame_end),
                "latency_ms": round(latency_ms, 3),
                "exposed_repositories": exposed_repositories,
                "hydra_candidate_paths": 0,
                "hydra_accepted_paths": 0,
                "hydra_rejected_sources": 0,
                "abstentions": [notice.reason for notice in response.abstentions],
                "historical_differs_current": set(exposed_repositories) != set(present_repositories),
                "query": response.as_json(),
            }
        )
    return {
        "mode": "live-hydra-temporal-query" if hydra_enabled else "offline-local-temporal-query",
        "hydra_enabled": hydra_enabled,
        "registry": registry,
        "package": package,
        "version": version,
        "present_repositories": present_repositories,
        "present_abstentions": [notice.reason for notice in present_response.abstentions],
        "frames": frames,
    }


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
        try:
            self._send_json(200, build_timeline_payload(self.settings, self.engine, query))
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})

    @staticmethod
    def _one(query: dict[str, list[str]], key: str, default: str) -> str:
        values = query.get(key)
        if not values or not values[0].strip():
            return default
        return values[0]

    @staticmethod
    def _config_string(section: JsonObject, key: str) -> str:
        value = section.get(key)
        if not isinstance(value, str):
            raise ValueError(f"timeline.{key} must be a string")
        return value

    def _send_json(self, status: int, value: JsonObject) -> None:
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
