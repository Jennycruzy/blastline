"""Record real public registry responses for offline parser tests.

This script never manufactures a fixture. It reads through the same cached
HTTP client as ingestion; use --refresh to obtain a fresh public response.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from blastline.config import Settings
from blastline.ingest.pipeline import RegistryIngestor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npm-package", action="append", default=[])
    parser.add_argument("--pypi-package", action="append", default=[])
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = Settings.load(root)
    ingestor = RegistryIngestor(settings)
    recordings = root / "cache" / "recordings"
    for name in args.npm_package:
        response = ingestor.npm.package(name, refresh=args.refresh)
        path = recordings / "npm" / f"{name.replace('/', '__')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.body)
        print(f"recorded real npm response {name} -> {path} cached={response.from_cache}")
    for name in args.pypi_package:
        response = ingestor.pypi.package(name, refresh=args.refresh)
        path = recordings / "pypi" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.body)
        print(f"recorded real PyPI response {name} -> {path} cached={response.from_cache}")
    if not args.npm_package and not args.pypi_package:
        raise SystemExit("provide at least one real package with --npm-package or --pypi-package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
