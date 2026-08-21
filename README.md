# BLASTLINE

[![Watch the 3-minute Blastline demo](https://img.youtube.com/vi/6ecqZBsBWkU/hqdefault.jpg)](https://www.youtube.com/watch?v=6ecqZBsBWkU)

[Watch the 3-minute Blastline demo on YouTube](https://www.youtube.com/watch?v=6ecqZBsBWkU)

When a package version is found to be compromised or vulnerable, Blastline answers which real services were exposed during the affected interval, which moved to a verified different resolution, and which retain unresolved current risk.

The enabling primitive is a bitemporal dependency graph. `t_valid` is when a resolution or malicious version was true in the world. `t_commit` is when Blastline learned it. Keeping those axes separate makes “who installed it while nobody knew?” a traversal instead of a guess.

The committed flagship incident is advisory-backed: the compromised npm version is `lodash@4.17.21`, examined over `[2021-02-20T15:42:16Z, 2026-08-01T00:00:00Z)`. Blastline follows that exact affected version; it does not treat every Lodash version as compromised.

## Judge's 90-second path

1. **The question:** who resolved the advisory-backed compromised `lodash@4.17.21` while the incident was active, and who still resolves it now? A package declaration or present-day dependency list cannot answer that.
2. **The evidence:** Blastline stores typed `Repository → Resolution → Version` paths in a bitemporal graph. `t_valid` says when a lockfile resolution was active; `t_commit` says when that evidence became known. Exposure requires the exact version and the relevant interval evidence to agree.
3. **The result:** the measured corpus contains 30 historically exposed repositories, 27 still-current resolutions, and 3 verified moves to a different version. Results retain the lockfile path, commit, resolution interval, and source/parser provenance.
4. **HydraDB’s role:** HydraDB retrieves candidate multi-hop paths and typed graph evidence. Blastline validates those candidates locally against the temporal lockfile graph before accepting them; retrieval is evidence to check, not the security answer itself.
5. **The limit:** the aligned `1.0000` score measures agreement with a graph-free reparse of the same snapshots, not universal parser correctness. The independent raw-lockfile holdout has 20 cases—10 positive and 10 negative—and all 20 pass. Invalid evidence becomes `unknown` through explicit abstention.

To reproduce the headline result after cloning, run the query and show only its summary:

```sh
make window REGISTRY=npm PKG=lodash VERSION=4.17.21 \
  FROM=2021-02-20T15:42:16Z TO=2026-08-01T00:00:00Z | tail -n 4
make verify-holdout
```

## What makes Blastline different

Blastline does not only ask what depends on a vulnerable package today. It reconstructs which repositories resolved the compromised version during the exposure window, follows the `Repository → Resolution → Version` path, and separates historical exposure from current state. HydraDB returns the candidate relationship path; Blastline accepts it only after checking the typed edge evidence and exact temporal predicates. When the path or its timestamps cannot be verified, Blastline abstains instead of reporting no current exposure.

## Verification first

The scorecard is generated from real OSV advisory records and real public GitHub lockfile history. Cases are selected by finding affected versions directly in the raw snapshot ledger, not by following graph `Resolution` edges. The graph-free observation oracle then reparses those immutable payloads. The corrected check covers 50 gradable temporal cases and 338 positive repository-pair decisions: TP=338, FP=0, FN=0, precision 1.0000, recall 1.0000. True negatives are not enumerated. A separate 20-case manually reviewed parser holdout contains 10 positive and 10 negative labels and passes all 20 cases. This is a small measured corpus, not an ecosystem-wide accuracy claim. The 1.0000 establishes that temporal interval intersection agrees with a graph-free reparse of the same snapshots. It does not establish parser correctness — the manual holdout is the independent check on that. Every recorded run preserves its graph fingerprint, commit SHA, denominator, abstentions, and misses in [`cache/verification/runs.jsonl`](cache/verification/runs.jsonl). Current-source failures remain itemized in [`cache/ingest-failures.jsonl`](cache/ingest-failures.jsonl).

The prior pre-fix scorecard is retained in the append-only ledger as TP=337, FP=103, FN=1: those 103 false positives came from comparing transitive reverse-blast-radius candidates at the window’s opening instant with the oracle’s direct lockfile exposure set, rather than using temporal interval intersection for the exposure case.

## Current measured snapshot

```text
historical exposure: 30 repositories resolved the advisory-backed compromised version npm:lodash@4.17.21 during the recorded vulnerability interval
current exposure: 27 repositories still resolve lodash@4.17.21
historical only: 3 repositories now have a verified different resolution
local verification: 50 gradable cases; TP=338; FP=0; FN=0; precision 1.0000; recall 1.0000
manual parser holdout: 20 of 20 reviewed labels pass (10 positive, 10 negative)
Hydra/local agreement: 10/10 cases; 0 false confirmations; 0 false omissions; 0 abstentions
Hydra flagship retrieval: 30/30 temporal paths accepted; 0 abstentions; 0 retrieval warnings
measured package coverage: npm 0.132512%; PyPI 0.000804%
graph fingerprint: a82daf6c4e1a957733d17a413740d256273cc19f950de209c9ab30a0a03f4722
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
make enrich-metadata
```

`make prepare-graph` is offline and deterministic with the committed recordings. It first unpacks the compressed registry cache and `data/graph.tar.zst` when available, verifies the graph fingerprint against the committed report, and otherwise restores the recorded seed (including the cached `vs-deploy` baseline required by the corpus manifest) and replays the corpus. The loose cache projection, `data/graph/` directory, and readiness markers are ignored. Graph-consuming Make targets invoke it automatically, so a fresh clone follows the same path before verification or a demo query.

The offline `window` command runs the exact local temporal oracle. `hydra-window` uses HydraDB graph-context recall for multi-hop navigation and paginated hosted collection retrieval for exhaustive typed candidates, then validates temporal evidence locally before accepting them. A graph-context outage is recorded as a warning; failure of the exhaustive hosted evidence path remains an error. Without a key, it abstains rather than presenting local output as Hydra-backed. In the committed demo, the historical set contains 30 repositories, the latest recorded state contains 27, and three are historical-only. The timeline begins before the first recorded resolution of the advisory-backed compromised `lodash@4.17.21` and grows from zero to the full historical set as the scrubber crosses real lockfile commits.

`make ingest` advances the cached incremental feeds. `make ingest-full` bootstraps npm from the supported paginated replication catalog, then enumerates the complete PyPI Simple index; both paths checkpoint progress and report every failed record. `make ingest-pypi-full` runs only the PyPI catalog path. Full ecosystem ingestion is network- and disk-bound, so the checked-in demo remains deliberately labeled as a measured partial corpus rather than a completeness claim.

`make discover-corpus` selects a reproducible corpus of real public GitHub lockfile histories from OSV-implicated package names. It requires an authenticated `GITHUB_TOKEN` or `GH_TOKEN`, enforces the configured minimum history and per-owner cap, documents the rule in [`docs/CORPUS.md`](docs/CORPUS.md), and records the selected repository list at the manifest path configured under `corpus`. `make ingest-corpus` then parses those real snapshots and reports distinct repositories, snapshots, resolutions, and failures together.

Abstention and coverage can be exercised against an input that cannot be parsed: `make check-lockfile LOCKFILE=/path/to/bad/package-lock.json REPOSITORY=example/bad VALID_FROM=2026-08-13T00:00:00Z`. Blastline retains that real repository as unknown, records the failure, and prints the changed `M of N` coverage instead of treating it as no exposure.

Live HydraDB calls require `HYDRA_DB_API_KEY`; tenant and sub-tenant defaults are in [`config/default.json`](config/default.json). Without credentials, the exact local projection remains runnable and the live call says `ABSTAINED`.

## What is implemented

- Reverse repository blast radius with repository paths and depth.
- Advisory-backed first affected version.
- Bitemporal window exposure, with historical/current comparison.
- Maintainer credential blast radius.
- Shared publisher/infrastructure relationships.
- Explainable name-plus-topology proximity scoring.
- Historically exposed repositories with unresolved current risk after resolution changes.
- Explicit abstention and `M` of `N` repository coverage on every query.
- Hydra-backed candidate-path retrieval with typed temporal evidence validation and a measured local/Hydra agreement scorecard.
- Cached, resumable npm/PyPI/OSV/GitHub ingestion with idempotent graph writes and content-addressed fingerprints.
- Strict parsers for npm `package-lock.json`, Yarn, pnpm, Poetry, and pinned requirements files.

`make enrich-metadata` selects a small representative slice. `make enrich-metadata-full` runs the resumable pass over all 5,654 existing graph packages selected by registry and package name; it created no version, dependency, or repository nodes. The full run matched 16,596 graph versions and produced usable maintainer metadata for 5,435 package outcomes, with 214 fetch errors, one empty metadata response, four packages without matching versions, and zero parse errors. It added 95,466 maintainer edges and 16,189 infrastructure edges; the resulting graph has 3,299 unique maintainer nodes and 2 publish-infrastructure nodes. The detailed outcome artifact is [`examples/metadata-enrichment-full.json`](examples/metadata-enrichment-full.json). Maintainer attribution remains explicitly incomplete for the failed or empty outcomes.

The `Resolution` node is deliberately first-class. A manifest range is not exposure: the same `^4.17.0` can resolve to a safe version, a compromised version, and a safe version again without the repository changing. Only a time-bounded resolution records those three states.

## Measured demo snapshot

The current local graph contains 5,693 packages, 17,007 versions, 3,299 unique maintainers, 2 publish-infrastructure records, 54 repositories, 204,976 resolutions, 10 advisories, and 941,169 edges. It includes the real-response demo slice plus 327 parsed snapshots from the reproducible 53-repository GitHub lockfile corpus. The corpus pass reported 10 failed records and no failed repositories; the append-only failure ledger currently contains 981 records across all ingestion runs. This is still a measured partial corpus, not an ecosystem-wide completeness claim; the resumable catalog paths are the route to broader coverage.

The generated incident artifact is [`examples/incident-report.json`](examples/incident-report.json). It includes the historical/current comparison, repositories with unresolved current risk, coverage, graph fingerprint, local verification scorecard, and Hydra status or agreement scorecard. The measured graph report is [`examples/coverage-report.json`](examples/coverage-report.json).

## Documentation

- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) argues for the graph and the `Resolution` node.
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md) describes the ground-truth protocol and misses.
- [`docs/HYDRA.md`](docs/HYDRA.md) identifies the HydraDB primitives used and what would be lost without them.
- [`docs/RFC_HYDRA_TEMPORAL_PREDICATES.md`](docs/RFC_HYDRA_TEMPORAL_PREDICATES.md) proposes interval predicates for graph-context retrieval.

## Attribution

Blastline uses the public [npm registry](https://github.com/npm/registry), the [npm replication changes feed](https://github.com/npm/replicate), the [PyPI JSON API](https://warehouse.pypa.io/api-reference/json.html), the [PyPI Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/), [OSV.dev](https://google.github.io/osv.dev/), and the [GitHub REST and raw-content APIs](https://docs.github.com/en/rest). Graph/context storage and graph-enriched recall use [HydraDB](https://docs.hydradb.com/api-reference), and the temporal edge model explicitly builds on its [graph-first architecture](https://docs.hydradb.com/essentials/architecture). The implementation uses Python 3.11 standard-library modules and is licensed under Apache 2.0.

## License

Blastline is licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
