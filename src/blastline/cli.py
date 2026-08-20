"""Command-line entry point for the currently completed milestones."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Settings
from .coverage_report import generate_coverage_report
from .errors import BlastlineError, ConfigurationError, ExternalCallError
from .hydra import HydraClient, load_hydra_config, response_success
from .ingest.pipeline import RegistryIngestor
from .infer.typosquat import TyposquatScorer
from .query.engine import QueryEngine
from .query.hydra_evidence import HydraWindowVerifier
from .query.types import QueryResponse
from .report import generate_incident_report
from .verify.grader import Verifier
from .verify.hydra_scorecard import HydraAgreementVerifier
from .verify.manual_holdout import ManualHoldoutVerifier
from .model import EdgeType, Node, NodeType, version_id
from .store import GraphStore
from .timeutil import format_time, now_utc, parse_time
from .json_types import require_bool, require_object, require_string


def root_directory() -> Path:
    return Path(__file__).resolve().parents[2]


def build_store(settings: Settings) -> GraphStore:
    return GraphStore(settings.path("graph", "directory"))


def build_hydra(settings: Settings) -> HydraClient:
    return HydraClient(load_hydra_config(settings.root, settings.values))


def hello(settings: Settings) -> int:
    store = GraphStore(settings.root / "data" / "m0-hello")
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


def hydra_init(settings: Settings) -> int:
    hydra = build_hydra(settings)
    if not hydra.live_enabled:
        raise ConfigurationError("hydra-init requires HYDRA_DB_API_KEY")
    try:
        status = hydra.database_status()
        print(f"HydraDB database exists: {hydra.config.tenant_id}")
    except ExternalCallError as exc:
        if "DATABASE_NOT_FOUND" not in str(exc):
            raise
        created = hydra.create_tenant([])
        if not response_success(created):
            raise BlastlineError("HydraDB did not accept database creation")
        print(f"HydraDB database creation accepted: {hydra.config.tenant_id}")
        status = None
    attempts = settings.integer("hydra", "database_ready_attempts")
    delay = settings.number("hydra", "database_poll_seconds")
    for attempt in range(attempts):
        if status is None or attempt > 0:
            status = hydra.database_status()
        infra = require_object(status.body.get("infra"), "Hydra database status.infra")
        ready = require_bool(infra.get("ready_for_ingestion"), "Hydra database status.infra.ready_for_ingestion")
        print(f"HydraDB readiness {attempt + 1}/{attempts}: {'ready' if ready else 'provisioning'}")
        if ready:
            return 0
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise BlastlineError("HydraDB database did not become ready before the configured wait limit")


def demo_timetravel(settings: Settings) -> int:
    store = build_store(settings)
    registry = settings.string("timetravel_demo", "registry")
    package = settings.string("timetravel_demo", "package")
    version = settings.string("timetravel_demo", "version")
    valid_at = parse_time(settings.string("timetravel_demo", "valid_at"), "timetravel_demo.valid_at")
    known_before = parse_time(settings.string("timetravel_demo", "known_before"), "timetravel_demo.known_before")
    known_after = parse_time(settings.string("timetravel_demo", "known_after"), "timetravel_demo.known_after")
    target_id = version_id(registry, package, version)
    before = store.incoming(target_id, EdgeType.RESOLVED_TO, valid_at=valid_at, commit_at=known_before)
    after = store.incoming(target_id, EdgeType.RESOLVED_TO, valid_at=valid_at, commit_at=known_after)
    print(f"bitemporal demo: {registry}:{package}@{version}, valid_at={format_time(valid_at)}")
    print(f"  as known at {format_time(known_before)}: {len(before)} relationship(s) — {'ABSTAIN' if not before else 'visible'}")
    print(f"  as known at {format_time(known_after)}: {len(after)} relationship(s) — {'ABSTAIN' if not after else 'visible'}")
    if not after:
        raise BlastlineError("real bitemporal demo has no post-commit Resolution edge")
    print(f"  append-only fingerprint: {store.fingerprint()}")
    return 0


def ingest(settings: Settings, args: argparse.Namespace) -> int:
    ingestor = RegistryIngestor(settings)
    refresh = bool(args.refresh)
    ran = False
    explicit_source = bool(
        args.full
        or args.npm_package
        or args.pypi_package
        or args.npm_changes
        or args.pypi_simple
        or args.pypi_full
        or args.github_repository
        or args.github_corpus
        or args.lockfile_path
        or args.osv_package
    )
    if args.npm_package:
        ingestor.print_report(ingestor.npm_packages(tuple(args.npm_package), refresh=refresh))
        ran = True
    if args.pypi_package:
        ingestor.print_report(ingestor.pypi_packages(tuple(args.pypi_package), refresh=refresh))
        ran = True
    if args.full or args.npm_changes or not explicit_source:
        limit = settings.integer("ingest", "npm_changes_limit")
        if args.full:
            ingestor.npm_catalog(settings.integer("ingest", "npm_catalog_page_limit"), refresh=refresh)
        else:
            ingestor.print_report(ingestor.npm_changes(limit, refresh=refresh))
        ran = True
    if args.full or args.pypi_simple or args.pypi_full or not explicit_source:
        limit = None if args.full or args.pypi_full else settings.integer("ingest", "pypi_simple_limit")
        ingestor.print_report(ingestor.pypi_simple(None if args.full else limit, refresh=refresh))
        ran = True
    if args.github_repository is not None:
        if args.github_path is None:
            raise ConfigurationError("--github-path is required with --github-repository")
        snapshots, resolutions, failures, fingerprint = ingestor.github_lockfile(
            args.github_repository,
            args.github_path,
            args.github_ref,
            args.github_ecosystem,
            refresh=refresh,
        )
        print(
            f"github-lockfile: parsed {snapshots} real snapshots and {resolutions} resolutions; "
            f"failed {failures}; graph fingerprint {fingerprint}"
        )
        ran = True
    if args.github_corpus:
        selected, snapshots, resolutions, failures, failed_repositories, fingerprint = ingestor.github_corpus(refresh=refresh)
        print(
            f"github-corpus: selected {selected} distinct repositories; parsed {snapshots} real snapshots; "
            f"{resolutions} resolutions; failed records {failures}; failed repositories {failed_repositories}; "
            f"graph fingerprint {fingerprint}"
        )
        print(f"distinct repositories in graph: {len(build_store(settings).nodes_of_type(NodeType.REPOSITORY))}")
        ran = True
    if args.lockfile_path is not None:
        if args.lockfile_repository is None:
            raise ConfigurationError("--lockfile-repository is required with --lockfile-path")
        if args.lockfile_valid_from is None:
            raise ConfigurationError("--lockfile-valid-from is required with --lockfile-path")
        resolutions, issues, failures, fingerprint = ingestor.local_lockfile(
            Path(args.lockfile_path),
            args.lockfile_repository,
            args.lockfile_ecosystem,
            args.lockfile_valid_from,
            args.lockfile_valid_to,
        )
        print(
            f"local-lockfile: parsed {resolutions} resolutions; itemized issues {issues}; "
            f"failed {failures}; graph fingerprint {fingerprint}"
        )
        coverage = query_engine(settings).coverage_report()
        print(coverage.human())
        ran = True
    if args.osv_package is not None:
        versions = tuple(args.osv_version) if args.osv_version else None
        matched, failures, fingerprint = ingestor.osv_package(args.osv_registry, args.osv_package, versions)
        print(
            f"osv: matched {matched} advisory/version relationships; failed {failures}; "
            f"graph fingerprint {fingerprint}"
        )
        ran = True
    if not ran:
        raise ConfigurationError("ingest requires a registry source selection")
    return 0


def measure_coverage(settings: Settings, refresh: bool) -> int:
    RegistryIngestor(settings).measure_registry_denominators(refresh=refresh)
    print("authoritative registry denominators recorded")
    return coverage_report(settings, False)


def enrich_metadata(settings: Settings, limit: int, registry: str | None, refresh: bool) -> int:
    artifact = RegistryIngestor(settings).enrich_metadata(limit, registry=registry, refresh=refresh)
    selected = artifact.get("selected_packages")
    results = artifact.get("results")
    if not isinstance(selected, list) or not isinstance(results, list):
        raise BlastlineError("metadata enrichment artifact has invalid shape")
    print(
        f"metadata enrichment: selected {len(selected)} packages; "
        f"artifact examples/metadata-enrichment.json; graph fingerprint {artifact['graph_fingerprint']}"
    )
    for result in results:
        print("  " + json.dumps(result, sort_keys=True))
    return 0


def enrich_metadata_full(
    settings: Settings,
    batch_size: int,
    registry: str | None,
    refresh: bool,
    max_packages: int | None,
) -> int:
    artifact = RegistryIngestor(settings).enrich_metadata_full(
        batch_size,
        registry=registry,
        refresh=refresh,
        max_packages=max_packages,
    )
    print(
        f"full metadata enrichment: {artifact['outcomes']['matched_versions']} matched versions; "
        f"artifact examples/metadata-enrichment-full.json; graph fingerprint {artifact['graph_fingerprint']}"
    )
    print(json.dumps(artifact["outcomes"], sort_keys=True))
    return 0


def discover_corpus(settings: Settings, refresh: bool) -> int:
    manifest = RegistryIngestor(settings).discover_github_corpus(refresh=refresh)
    print(
        f"github-corpus discovery: {len(manifest.selected)} selected repositories, "
        f"{len(manifest.rejected)} rejected candidates, "
        f"{len(manifest.implicated_packages)} advisory-implicated packages"
    )
    print("manifest: " + str(settings.path("corpus", "manifest").relative_to(settings.root)))
    for item in manifest.selected:
        print(f"  SELECTED {item.full_name}:{item.path} ({len(item.history_shas)} historical commits)")
    return 0


def publish_graph(settings: Settings) -> int:
    nodes, edges, fingerprint = RegistryIngestor(settings).publish_existing_graph()
    print(f"HydraDB graph upserted: {nodes} nodes, {edges} edges; graph fingerprint {fingerprint}")
    return 0


def publish_flagship(settings: Settings) -> int:
    nodes, edges, fingerprint = RegistryIngestor(settings).publish_flagship_graph()
    print(f"HydraDB flagship evidence upserted: {nodes} nodes, {edges} edges; graph fingerprint {fingerprint}")
    return 0


def publish_verification(settings: Settings) -> int:
    nodes, edges, fingerprint = RegistryIngestor(settings).publish_verification_graph()
    print(f"HydraDB verification evidence upserted: {nodes} nodes, {edges} edges; graph fingerprint {fingerprint}")
    return 0


def query_engine(settings: Settings) -> QueryEngine:
    return QueryEngine(build_store(settings), settings)


def print_query(response: QueryResponse, as_json: bool) -> None:
    if as_json:
        print(json.dumps(response.as_json(), sort_keys=True, indent=2))
    else:
        print(response.human())


def parse_optional_time(value: str | None, context: str) -> datetime | None:
    if value is None:
        return None
    return parse_time(value, context)


def run_query(settings: Settings, args: argparse.Namespace) -> int:
    engine = query_engine(settings)
    registry = args.registry if hasattr(args, "registry") else "npm"
    if args.command == "blast":
        response = engine.blast_radius(
            registry,
            args.package,
            args.version,
            parse_optional_time(args.valid_at, "blast valid_at"),
            parse_optional_time(args.known_at, "blast known_at"),
        )
    elif args.command == "window":
        start = parse_time(args.from_time, "window from")
        end = parse_time(args.to_time, "window to")
        response = engine.window_exposure(registry, args.package, args.version, (start, end), parse_optional_time(args.known_at, "window known_at"))
        current = engine.current_exposure(registry, args.package, args.version)
        historical_repositories = {item.get("repository") for item in response.results if isinstance(item.get("repository"), str)}
        current_repositories = {item.get("repository") for item in current.results if isinstance(item.get("repository"), str)}
        comparison: JsonObject = {
            "historical_repositories": sorted(historical_repositories),
            "current_repositories": sorted(current_repositories),
            "historical_only": sorted(historical_repositories - current_repositories),
            "current_only": sorted(current_repositories - historical_repositories),
        }
        if args.json:
            payload = response.as_json()
            payload["present_day"] = current.as_json()
            payload["comparison"] = comparison
            print(json.dumps(payload, sort_keys=True, indent=2))
        else:
            print_query(response, False)
            print(
                "present-day comparison: "
                f"historical={len(historical_repositories)} repositories, current={len(current_repositories)}; "
                f"historical-only={len(historical_repositories - current_repositories)}, "
                f"current-only={len(current_repositories - historical_repositories)}"
            )
            if current.abstentions:
                print(f"present-day abstentions: {len(current.abstentions)}")
                for notice in current.abstentions:
                    print(f"  {notice.scope}: {notice.reason}")
        return 0
    elif args.command == "hydra-window":
        start = parse_time(args.from_time, "hydra-window from")
        end = parse_time(args.to_time, "hydra-window to")
        known_at = parse_optional_time(args.known_at, "hydra-window known_at")
        hydra_result = HydraWindowVerifier(
            build_hydra(settings),
            build_store(settings),
            engine,
            settings.integer("hydra", "candidate_result_limit"),
        ).run(registry, args.package, args.version, (start, end), known_at)
        hydra_record = hydra_result.as_json()
        hydra_record["graph_fingerprint"] = build_store(settings).fingerprint()
        hydra_record["recorded_at"] = format_time(now_utc())
        hydra_record_path = settings.root / "cache" / "verification" / "hydra-window.jsonl"
        hydra_record_path.parent.mkdir(parents=True, exist_ok=True)
        with hydra_record_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(hydra_record, sort_keys=True, separators=(",", ":")) + "\n")
        if args.json:
            print(json.dumps(hydra_record, sort_keys=True, indent=2))
        else:
            print("BLASTLINE HYDRA WINDOW QUERY")
            print(f"target: {hydra_result.target}")
            print(f"valid_at: {format_time(hydra_result.valid_at)}")
            print(f"window_end: {format_time(hydra_result.window_end)}")
            print(f"known_at: {format_time(hydra_result.known_at) if hydra_result.known_at is not None else 'latest-known'}")
            print(f"HydraDB candidate paths: {len(hydra_result.candidate_paths)}")
            print(f"HydraDB relations inspected: {len(hydra_result.inspected_relations)}")
            print(f"temporal paths accepted: {len(hydra_result.accepted_results)}")
            print(f"candidate sources rejected: {len(hydra_result.rejected_source_ids)}")
            print(f"abstentions: {len(hydra_result.abstentions)}")
            print(f"retrieval warnings: {len(hydra_result.retrieval_warnings)}")
            for warning in hydra_result.retrieval_warnings:
                print(f"  {warning}")
            print(f"historical repositories: {len(hydra_result.historical_repositories)}")
            print(f"current repositories: {len(hydra_result.current_repositories)}")
            print(
                "historical result differs from current result: "
                + ("PASS" if set(hydra_result.historical_repositories) != set(hydra_result.current_repositories) else "FAIL")
            )
            for item in hydra_result.accepted_results:
                repository = item.get("repository", "unknown")
                path = item.get("resolution", "unknown")
                print(f"EXPOSED: {repository} via {path}")
                print("  path: Repository → Resolution → Version")
            if hydra_result.abstentions:
                for reason in hydra_result.abstentions:
                    print(f"  ABSTAINED: {reason}")
            agreement = hydra_result.local_hydra_agreement
            print(f"LOCAL/HYDRA AGREEMENT: {'PASS' if agreement is True else 'FAIL' if agreement is False else 'ABSTAINED'}")
            print(f"latency_ms: {hydra_result.latency_ms:.3f}")
            print(f"append-only Hydra window run: {hydra_record_path}")
        return 0
    elif args.command == "first-affected":
        response = engine.first_affected_version(registry, args.package, args.version, parse_optional_time(args.known_at, "first-affected known_at"))
    elif args.command == "maintainer-risk":
        response = engine.maintainer_risk(args.maintainer, parse_optional_time(args.valid_at, "maintainer valid_at"))
    elif args.command == "shared-infra":
        response = engine.shared_infrastructure(registry, args.package, args.version, parse_optional_time(args.valid_at, "shared-infra valid_at"))
    elif args.command == "still-dirty":
        start = parse_time(args.from_time, "still-dirty from")
        end = parse_time(args.to_time, "still-dirty to")
        response = engine.still_dirty(registry, args.package, args.version, (start, end), parse_optional_time(args.as_of, "still-dirty as_of"))
    elif args.command == "coverage":
        response = engine.coverage_report()
    elif args.command == "typosquats":
        response = TyposquatScorer(build_store(settings), settings).score(registry, args.package, parse_optional_time(args.as_of, "typosquats as_of"))
    else:
        raise ConfigurationError(f"query command is not implemented: {args.command}")
    print_query(response, args.json)
    return 0


def verify(settings: Settings, as_json: bool, record: bool = True) -> int:
    verifier = Verifier(build_store(settings), settings)
    scorecard = verifier.grade()
    if as_json:
        print(json.dumps(scorecard.as_json(), sort_keys=True, indent=2))
    else:
        print(scorecard.human())
    if record:
        path = verifier.record(scorecard)
        if not as_json:
            print(f"append-only verification run: {path}")
    return 0


def hydra_verify(settings: Settings, as_json: bool) -> int:
    verifier = HydraAgreementVerifier(build_store(settings), settings)
    scorecard = verifier.grade()
    path = verifier.record(scorecard)
    if as_json:
        print(json.dumps(scorecard.as_json(), sort_keys=True, indent=2))
    else:
        print(scorecard.human())
        print(f"append-only Hydra agreement run: {path}")
    return 0


def verify_holdout(settings: Settings, as_json: bool) -> int:
    scorecard = ManualHoldoutVerifier(settings).grade()
    if as_json:
        print(json.dumps(scorecard.as_json(), sort_keys=True, indent=2))
    else:
        print(scorecard.human())
    return 0


def report(settings: Settings, as_json: bool) -> int:
    artifact, payload = generate_incident_report(settings)
    if as_json:
        print(json.dumps(payload, sort_keys=True, indent=2))
    else:
        incident = payload["incident"]
        if not isinstance(incident, dict):
            raise BlastlineError("generated incident report has invalid incident section")
        print(
            f"incident report: {incident.get('package')}@{incident.get('version')} "
            f"from {incident.get('window', {}).get('from')} to {incident.get('window', {}).get('to')}"
        )
        comparison = payload["comparison"]
        print(f"historical exposure: {len(comparison['historical_repositories'])} repository/repositories")
        print(f"current exposure: {len(comparison['present_repositories'])} repository/repositories")
        print(f"historical only: {len(comparison['historical_only'])} repository/repositories")
        print(f"unresolved current risk: {len(payload['still_dirty']['results'])} repository/repositories")
        print(f"verification: {payload['verification']['precision']} precision, {payload['verification']['recall']} recall")
        print(
            "manual parser holdout: "
            f"{payload['manual_parser_holdout']['passed']} of {payload['manual_parser_holdout']['cases']} passed"
        )
        print(f"generated artifact: {artifact.relative_to(settings.root)}")
    return 0


def coverage_report(settings: Settings, as_json: bool) -> int:
    artifact, payload = generate_coverage_report(settings)
    if as_json:
        print(json.dumps(payload, sort_keys=True, indent=2))
    else:
        print(f"coverage artifact: {artifact.relative_to(settings.root)}")
        for registry, value in payload["registries"].items():
            if not isinstance(value, dict):
                raise BlastlineError(f"coverage report registry {registry} is invalid")
            coverage_value = value.get("package_name_coverage_percent")
            coverage_text = coverage_value if coverage_value is not None else "not-measured"
            print(
                f"{registry}: observed {value.get('packages')} packages, "
                f"{value.get('versions')} versions, "
                f"coverage {coverage_text}"
            )
        print(f"failures: {payload['failures']['total']}")
    return 0


def add_output_options(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def add_package_options(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument("--registry", default="npm")
    command_parser.add_argument("--package", required=True)
    command_parser.add_argument("--version", required=True)


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="blastline")
    subparsers = command_parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("hello")
    subparsers.add_parser("hydra-init")
    subparsers.add_parser("demo-timetravel")
    corpus_parser = subparsers.add_parser("discover-corpus")
    corpus_parser.add_argument("--refresh", action="store_true")
    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--full", action="store_true", help="drain the npm feed and enumerate the full PyPI simple index")
    ingest_parser.add_argument("--npm-package", action="append", default=[])
    ingest_parser.add_argument("--pypi-package", action="append", default=[])
    ingest_parser.add_argument("--npm-changes", action="store_true")
    ingest_parser.add_argument("--pypi-simple", action="store_true")
    ingest_parser.add_argument("--pypi-full", action="store_true", help="enumerate and resume the complete PyPI Simple index")
    ingest_parser.add_argument("--github-repository")
    ingest_parser.add_argument("--github-corpus", action="store_true")
    ingest_parser.add_argument("--github-path")
    ingest_parser.add_argument("--github-ref", default="main")
    ingest_parser.add_argument("--github-ecosystem", default="npm")
    ingest_parser.add_argument("--lockfile-path")
    ingest_parser.add_argument("--lockfile-repository")
    ingest_parser.add_argument("--lockfile-ecosystem", default="npm")
    ingest_parser.add_argument("--lockfile-valid-from")
    ingest_parser.add_argument("--lockfile-valid-to")
    ingest_parser.add_argument("--osv-package")
    ingest_parser.add_argument("--osv-registry", default="npm")
    ingest_parser.add_argument("--osv-version", action="append", default=[])
    ingest_parser.add_argument("--refresh", action="store_true")
    measure_coverage_parser = subparsers.add_parser("measure-coverage")
    measure_coverage_parser.add_argument("--refresh", action="store_true")
    metadata_parser = subparsers.add_parser("enrich-metadata")
    metadata_parser.add_argument("--limit", type=int, default=24)
    metadata_parser.add_argument("--registry", choices=("npm", "pypi"))
    metadata_parser.add_argument("--refresh", action="store_true")
    full_metadata_parser = subparsers.add_parser("enrich-metadata-full")
    full_metadata_parser.add_argument("--batch-size", type=int, default=50)
    full_metadata_parser.add_argument("--max-packages", type=int)
    full_metadata_parser.add_argument("--registry", choices=("npm", "pypi"))
    full_metadata_parser.add_argument("--refresh", action="store_true")
    subparsers.add_parser("publish-graph")
    subparsers.add_parser("publish-flagship")
    subparsers.add_parser("publish-verification")
    blast_parser = subparsers.add_parser("blast")
    add_package_options(blast_parser)
    blast_parser.add_argument("--valid-at")
    blast_parser.add_argument("--known-at")
    add_output_options(blast_parser)
    window_parser = subparsers.add_parser("window")
    add_package_options(window_parser)
    window_parser.add_argument("--from", dest="from_time", required=True)
    window_parser.add_argument("--to", dest="to_time", required=True)
    window_parser.add_argument("--known-at")
    add_output_options(window_parser)
    hydra_window_parser = subparsers.add_parser("hydra-window")
    add_package_options(hydra_window_parser)
    hydra_window_parser.add_argument("--from", dest="from_time", required=True)
    hydra_window_parser.add_argument("--to", dest="to_time", required=True)
    hydra_window_parser.add_argument("--known-at")
    add_output_options(hydra_window_parser)
    first_parser = subparsers.add_parser("first-affected")
    add_package_options(first_parser)
    first_parser.add_argument("--known-at")
    add_output_options(first_parser)
    maintainer_parser = subparsers.add_parser("maintainer-risk")
    maintainer_parser.add_argument("--maintainer", required=True)
    maintainer_parser.add_argument("--valid-at")
    add_output_options(maintainer_parser)
    infra_parser = subparsers.add_parser("shared-infra")
    add_package_options(infra_parser)
    infra_parser.add_argument("--valid-at")
    add_output_options(infra_parser)
    dirty_parser = subparsers.add_parser("still-dirty")
    add_package_options(dirty_parser)
    dirty_parser.add_argument("--from", dest="from_time", required=True)
    dirty_parser.add_argument("--to", dest="to_time", required=True)
    dirty_parser.add_argument("--as-of")
    add_output_options(dirty_parser)
    typo_parser = subparsers.add_parser("typosquats")
    typo_parser.add_argument("--registry", default="npm")
    typo_parser.add_argument("--package", required=True)
    typo_parser.add_argument("--as-of")
    add_output_options(typo_parser)
    coverage_parser = subparsers.add_parser("coverage")
    add_output_options(coverage_parser)
    coverage_report_parser = subparsers.add_parser("coverage-report")
    add_output_options(coverage_report_parser)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--no-record", action="store_true", help="check without appending a scorecard artifact")
    add_output_options(verify_parser)
    holdout_parser = subparsers.add_parser("verify-holdout")
    add_output_options(holdout_parser)
    hydra_verify_parser = subparsers.add_parser("hydra-verify")
    add_output_options(hydra_verify_parser)
    report_parser = subparsers.add_parser("report")
    add_output_options(report_parser)
    return command_parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.load(root_directory())
    if args.command == "hello":
        return hello(settings)
    if args.command == "hydra-init":
        return hydra_init(settings)
    if args.command == "demo-timetravel":
        return demo_timetravel(settings)
    if args.command == "ingest":
        return ingest(settings, args)
    if args.command == "discover-corpus":
        return discover_corpus(settings, args.refresh)
    if args.command == "measure-coverage":
        return measure_coverage(settings, args.refresh)
    if args.command == "enrich-metadata":
        return enrich_metadata(settings, args.limit, args.registry, args.refresh)
    if args.command == "enrich-metadata-full":
        return enrich_metadata_full(settings, args.batch_size, args.registry, args.refresh, args.max_packages)
    if args.command == "publish-graph":
        return publish_graph(settings)
    if args.command == "publish-flagship":
        return publish_flagship(settings)
    if args.command == "publish-verification":
        return publish_verification(settings)
    if args.command == "verify":
        return verify(settings, args.json, not args.no_record)
    if args.command == "verify-holdout":
        return verify_holdout(settings, args.json)
    if args.command == "hydra-verify":
        return hydra_verify(settings, args.json)
    if args.command == "report":
        return report(settings, args.json)
    if args.command == "coverage-report":
        return coverage_report(settings, args.json)
    if args.command in {
        "blast",
        "window",
        "hydra-window",
        "first-affected",
        "maintainer-risk",
        "shared-infra",
        "still-dirty",
        "typosquats",
        "coverage",
    }:
        return run_query(settings, args)
    raise ConfigurationError(f"command is not implemented yet: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BlastlineError as exc:
        print(f"blastline: ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
