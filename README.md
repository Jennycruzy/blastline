# BLASTLINE

When a package version is found to be compromised or vulnerable, Blastline answers which real services were exposed during the affected interval, which moved to a verified different resolution, and which retain unresolved current risk.

The enabling primitive is a bitemporal dependency graph. `t_valid` is when a resolution or malicious version was true in the world. `t_commit` is when Blastline learned it. Keeping those axes separate makes “who installed it while nobody knew?” a traversal instead of a guess.

## What makes Blastline different

Blastline does not only ask what depends on a vulnerable package today. It reconstructs which repositories resolved the compromised version during the exposure window, follows the `Repository → Resolution → Version` path, and separates historical exposure from current state. HydraDB returns the candidate relationship path; Blastline accepts it only after checking the typed edge evidence and exact temporal predicates. When the path or its timestamps cannot be verified, Blastline abstains instead of reporting no current exposure.

## Verification first

The scorecard is generated from real OSV advisory records and real public GitHub lockfile history. Cases are selected by finding affected versions directly in the raw snapshot ledger, not by following graph `Resolution` edges. The graph-free observation oracle then reparses those immutable payloads. The current check covers 50 gradable temporal cases and 441 positive repository-pair decisions: TP=337, FP=103, FN=1, precision 0.7659, recall 0.9970. True negatives are not enumerated. A separate four-case manually reviewed parser holdout passes two positive and two negative labels. This is a small measured corpus, not an ecosystem-wide accuracy claim. Every recorded run preserves its graph fingerprint, commit SHA, denominator, abstentions, and misses in [`cache/verification/runs.jsonl`](cache/verification/runs.jsonl). Current-source failures remain itemized in [`cache/ingest-failures.jsonl`](cache/ingest-failures.jsonl).

## Current measured snapshot

```text
historical exposure: 30 repositories resolved lodash@4.17.21 during the recorded vulnerability interval
current exposure: 27 repositories still resolve lodash@4.17.21
historical only: 3 repositories now have a verified different resolution
local verification: 50 gradable cases; TP=337; FP=103; FN=1; precision 0.7659; recall 0.9970
manual parser holdout: 4 of 4 reviewed labels pass
Hydra/local agreement: ABSTAINED — HYDRA_DB_API_KEY is not set
measured package coverage: npm 0.132512%; PyPI 0.000804%
graph fingerprint: b5fa455a5d3eea7bb0cda7e6a882e1ecccdb1fa16631889e16e711126a346619
```

The package coverage percentages are measured against the authoritative npm `_all_docs.total_rows` and PyPI Simple index counts captured by `make measure-coverage`. They describe the submitted graph, not ecosystem-wide completeness.

## Run it

The repository has no third-party runtime dependency. The first smoke check is local and offline:

```sh
make hello
make test
```

For the live HydraDB path, set `HYDRA_DB_API_KEY` only in the shell that runs the command, then provision the configured database and publish the real flagship evidence subgraph:

```sh
make hydra-init
make publish-flagship
make publish-verification
make hydra-window REGISTRY=npm PKG=lodash VERSION=4.17.21 \
  FROM=2021-02-20T15:42:16Z TO=2026-08-01T00:00:00Z
```

Rebuild or unpack the offline demo graph from recorded real registry responses. The loose graph projection is ignored, while a compressed snapshot is committed below GitHub's file limit; the raw recordings, corpus manifest, snapshot ledger, and verification evidence remain committed:

```sh
make prepare-graph
make window REGISTRY=npm PKG=lodash VERSION=4.17.21 \
  FROM=2021-02-20T15:42:16Z TO=2026-08-01T00:00:00Z
make publish-graph
make verify
make verify-holdout
make hydra-verify
make measure-coverage
make report
```

`make prepare-graph` is offline and deterministic with the committed recordings. It first unpacks the compressed registry cache and `data/graph.tar.zst` when available, verifies the graph fingerprint against the committed report, and otherwise restores the recorded seed (including the cached `vs-deploy` baseline required by the corpus manifest) and replays the corpus. The loose cache projection, `data/graph/` directory, and readiness markers are ignored. Graph-consuming Make targets invoke it automatically, so a fresh clone follows the same path before verification or a demo query.

The offline `window` command runs the exact local temporal oracle. `hydra-window` uses HydraDB graph-context recall and source-level relation inspection to obtain candidate paths, then validates their typed temporal evidence locally before accepting them. Without a key, it abstains rather than presenting local output as Hydra-backed. In the committed demo, the historical set contains 30 repositories, the latest recorded state contains 27, and three are historical-only. The timeline begins before the first recorded `lodash@4.17.21` resolution and grows from zero to the full historical set as the scrubber crosses real lockfile commits.

`make ingest` advances the cached incremental feeds. `make ingest-full` bootstraps npm from the supported paginated replication catalog, then enumerates the complete PyPI Simple index; both paths checkpoint progress and report every failed record. `make ingest-pypi-full` runs only the PyPI catalog path. Full ecosystem ingestion is network- and disk-bound, so the checked-in demo remains deliberately labeled as a measured partial corpus rather than a completeness claim.

`make discover-corpus` selects a reproducible corpus of real public GitHub lockfile histories from OSV-implicated package names. It requires an authenticated `GITHUB_TOKEN` or `GH_TOKEN`, enforces the configured minimum history and per-owner cap, documents the rule in [`docs/CORPUS.md`](docs/CORPUS.md), and records the selected repository list at the manifest path configured under `corpus`. `make ingest-corpus` then parses those real snapshots and reports distinct repositories, snapshots, resolutions, and failures together.

Q8 can be exercised against an input that cannot be parsed: `make check-lockfile LOCKFILE=/path/to/bad/package-lock.json REPOSITORY=example/bad VALID_FROM=2026-08-13T00:00:00Z`. Blastline retains that real repository as unknown, records the failure, and prints the changed `M of N` coverage instead of treating it as no exposure.

Live HydraDB calls require `HYDRA_DB_API_KEY`; tenant and sub-tenant defaults are in [`config/default.json`](config/default.json). Without credentials, the exact local projection remains runnable and the live call says `ABSTAINED`.

## What is implemented

- Q1 reverse blast radius with repository paths and depth.
- Q2 advisory-backed first affected version.
- Q3 bitemporal window exposure, with historical/current comparison.
- Q4 maintainer credential blast radius.
- Q5 shared publisher/infrastructure relationships.
- Q6 explainable name-plus-topology proximity scoring.
- Q7 historically exposed repositories with unresolved current risk after resolution changes.
- Q8 explicit abstention and `M` of `N` repository coverage on every query.
- Hydra-backed candidate-path retrieval with typed temporal evidence validation and a measured local/Hydra agreement scorecard.
- Cached, resumable npm/PyPI/OSV/GitHub ingestion with idempotent graph writes and content-addressed fingerprints.
- Strict parsers for npm `package-lock.json`, Yarn, pnpm, Poetry, and pinned requirements files.

The `Resolution` node is deliberately first-class. A manifest range is not exposure: the same `^4.17.0` can resolve to a safe version, a compromised version, and a safe version again without the repository changing. Only a time-bounded resolution records those three states.

## Measured demo snapshot

The current local graph contains 5,693 packages, 17,007 versions, 8 maintainers, 2 publish-infrastructure records, 54 repositories, 204,976 resolutions, 10 advisories, and 814,455 edges. It includes the real-response demo slice plus 327 parsed snapshots from the reproducible 53-repository GitHub lockfile corpus. The corpus pass reported 10 failed records and no failed repositories; the append-only failure ledger currently contains 981 records across all ingestion runs. This is still a measured partial corpus, not an ecosystem-wide completeness claim; the resumable catalog paths are the route to broader coverage.

The generated incident artifact is [`examples/incident-report.json`](examples/incident-report.json). It includes the historical/current comparison, repositories with unresolved current risk, coverage, graph fingerprint, local verification scorecard, and Hydra status or agreement scorecard. The measured graph report is [`examples/coverage-report.json`](examples/coverage-report.json).

## Documentation

- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) argues for the graph and the `Resolution` node.
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md) describes the ground-truth protocol and misses.
- [`docs/HYDRA.md`](docs/HYDRA.md) identifies the HydraDB primitives used and what would be lost without them.
- [`docs/RFC_HYDRA_TEMPORAL_PREDICATES.md`](docs/RFC_HYDRA_TEMPORAL_PREDICATES.md) proposes interval predicates for graph-context retrieval.

## Attribution

Blastline uses the public [npm registry](https://github.com/npm/registry), the [npm replication changes feed](https://github.com/npm/replicate), the [PyPI JSON API](https://warehouse.pypa.io/api-reference/json.html), the [PyPI Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/), [OSV.dev](https://google.github.io/osv.dev/), and the [GitHub REST and raw-content APIs](https://docs.github.com/en/rest). Graph/context storage and graph-enriched recall use [HydraDB](https://docs.hydradb.com/api-reference), and the temporal edge model explicitly builds on its [graph-first architecture](https://docs.hydradb.com/essentials/architecture). The implementation uses Python 3.11 standard-library modules and is licensed under Apache 2.0.

Development disclosure: Blastline was built with AI coding assistance. Its security claims are derived from committed source evidence and reproducible checks, not generated prose.
