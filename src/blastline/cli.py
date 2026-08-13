"""Command-line entry point for the currently completed milestones."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .errors import BlastlineError, ConfigurationError
from .hydra import HydraClient, load_hydra_config, response_success
from .ingest.pipeline import RegistryIngestor
from .model import Edge, EdgeType, Node, NodeType, TimeInterval
from .store import GraphStore
from .timeutil import format_time, now_utc, parse_time


def root_directory() -> Path:
    return Path(__file__).resolve().parents[2]


def build_store(settings: Settings) -> GraphStore:
    return GraphStore(settings.path("graph", "directory"))


def build_hydra(settings: Settings) -> HydraClient:
    return HydraClient(load_hydra_config(settings.root, settings.values))


def hello(settings: Settings) -> int:
    store = build_store(settings)
    node = Node("hello:blastline", NodeType.REPOSITORY, {"name": "hello", "source": "M0"})
    store.add_nodes([node])
    stored = store.node(node.node_id)
    if stored is None:
        raise BlastlineError("local graph read-back failed")
    print(f"local Hydra projection: wrote and read {stored.node_id} ({stored.node_type.value})")
    hydra = build_hydra(settings)
    if not hydra.live_enabled:
        print("live HydraDB: ABSTAINED — HYDRA_DB_API_KEY is not set")
        return 0
    response = hydra.add_memory(node.node_id, json.dumps(node.as_json(), sort_keys=True), node.attributes)
    if not response_success(response):
        raise BlastlineError("HydraDB did not accept the M0 node")
    read_back = hydra.list_source(node.node_id)
    print(f"live HydraDB: queued {node.node_id}; list response cached={read_back.from_cache}")
    return 0


def demo_timetravel(settings: Settings) -> int:
    store = build_store(settings)
    first = parse_time("2026-08-13T09:00:00Z", "demo start")
    second = parse_time("2026-08-13T09:05:00Z", "demo update")
    query_time = parse_time("2026-08-13T09:06:00Z", "demo query")
    repository = Node("repository:demo:payments", NodeType.REPOSITORY, {"name": "payments"})
    vulnerable = Node("version:npm:demo-lib@1.0.0", NodeType.VERSION, {"package": "demo-lib", "version": "1.0.0"})
    fixed = Node("version:npm:demo-lib@1.0.1", NodeType.VERSION, {"package": "demo-lib", "version": "1.0.1"})
    store.add_nodes([repository, vulnerable, fixed])
    old_edge = Edge.create(
        repository.node_id,
        EdgeType.RESOLVED_TO,
        vulnerable.node_id,
        TimeInterval(first, second),
        first,
        {"lockfile": "package-lock.json", "source": "demo"},
    )
    new_edge = Edge.create(
        repository.node_id,
        EdgeType.RESOLVED_TO,
        fixed.node_id,
        TimeInterval(second, None),
        query_time,
        {"lockfile": "package-lock.json", "source": "demo"},
    )
    store.add_edges([old_edge, new_edge])
    before_learning = store.outgoing(repository.node_id, EdgeType.RESOLVED_TO, query_time, first)
    after_learning = store.outgoing(repository.node_id, EdgeType.RESOLVED_TO, query_time, query_time)
    print("bitemporal demo: repository=payments, valid_at=2026-08-13T09:06:00Z")
    print(f"  as known at 09:00: {len(before_learning)} relationship(s) — {before_learning[0].target_id if before_learning else 'ABSTAIN'}")
    print(f"  as known at 09:03: {len(after_learning)} relationship(s) — {after_learning[0].target_id if after_learning else 'ABSTAIN'}")
    print(f"  append-only fingerprint: {store.fingerprint()}")
    return 0


def ingest(settings: Settings, args: argparse.Namespace) -> int:
    ingestor = RegistryIngestor(settings)
    refresh = bool(args.refresh)
    ran = False
    explicit_source = bool(args.npm_package or args.pypi_package or args.npm_changes or args.pypi_simple)
    if args.npm_package:
        ingestor.print_report(ingestor.npm_packages(tuple(args.npm_package), refresh=refresh))
        ran = True
    if args.pypi_package:
        ingestor.print_report(ingestor.pypi_packages(tuple(args.pypi_package), refresh=refresh))
        ran = True
    if args.npm_changes or not explicit_source:
        limit = settings.integer("ingest", "npm_changes_limit")
        ingestor.print_report(ingestor.npm_changes(limit, refresh=refresh))
        ran = True
    if args.pypi_simple or not explicit_source:
        limit = settings.integer("ingest", "pypi_simple_limit")
        ingestor.print_report(ingestor.pypi_simple(limit, refresh=refresh))
        ran = True
    if not ran:
        raise ConfigurationError("ingest requires a registry source selection")
    return 0


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="blastline")
    subparsers = command_parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("hello")
    subparsers.add_parser("demo-timetravel")
    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--npm-package", action="append", default=[])
    ingest_parser.add_argument("--pypi-package", action="append", default=[])
    ingest_parser.add_argument("--npm-changes", action="store_true")
    ingest_parser.add_argument("--pypi-simple", action="store_true")
    ingest_parser.add_argument("--refresh", action="store_true")
    return command_parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.load(root_directory())
    if args.command == "hello":
        return hello(settings)
    if args.command == "demo-timetravel":
        return demo_timetravel(settings)
    if args.command == "ingest":
        return ingest(settings, args)
    raise ConfigurationError(f"command is not implemented yet: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BlastlineError as exc:
        print(f"blastline: ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
