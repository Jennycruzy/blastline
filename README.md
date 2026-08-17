# BLASTLINE

When a package is compromised at 09:00, Blastline answers which real services were exposed by 09:06, which were exposed only during the incident window, and which are still dirty after their current lockfile looks clean.

The enabling primitive is a bitemporal dependency graph. `t_valid` is when a resolution or malicious version was true in the world. `t_commit` is when Blastline learned it. Keeping those axes separate makes “who installed it while nobody knew?” a traversal instead of a guess.

## Verification first

The committed scorecard is generated from real OSV advisory records and real public GitHub lockfile history. The latest recorded run covers 15 gradable cases: TP=15, FP=0, FN=0, precision 1.0000, recall 1.0000. This is a small measured corpus, not an ecosystem-wide accuracy claim. Every run records its graph fingerprint, commit SHA, denominator, abstentions, and misses in [`cache/verification/runs.jsonl`](cache/verification/runs.jsonl). Current-source failures remain itemized in [`cache/ingest-failures.jsonl`](cache/ingest-failures.jsonl).

## Current measured snapshot

```text
historical exposure: npm/cli during the recorded incident window
current exposure: none
local verification: 15 gradable cases; precision 1.0000; recall 1.0000
Hydra/local agreement: 15/15 PASS; false confirmations 0; false omissions 0; abstentions 0
measured package coverage: npm 0.020299%; PyPI 0.000804%
graph fingerprint: 812899da3f19920100bfd0855a2cb2279e3389fd4db83b8dd6fc2a6ea0275535
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
make hydra-window REGISTRY=npm PKG=write-file-atomic VERSION=7.0.1 \
  FROM=2026-07-08T18:00:00Z TO=2026-07-08T20:30:00Z
```

Rebuild the committed offline demo graph from recorded real registry responses:

```sh
make demo
make window REGISTRY=npm PKG=write-file-atomic VERSION=7.0.1 \
  FROM=2026-07-08T18:00:00Z TO=2026-07-08T20:30:00Z
make publish-graph
make verify
make hydra-verify
make measure-coverage
make report
```

The offline `window` command runs the exact local temporal oracle. `hydra-window` uses HydraDB graph-context recall and source-level relation inspection to obtain candidate paths, then validates their typed temporal evidence locally before accepting them. Without a key, it abstains rather than presenting local output as Hydra-backed. In the committed demo, the historical set contains `npm/cli` and the present-day set is empty. The timeline starts before the real `npm/cli` resolution commit, so its exposed set grows from zero to one as the scrubber crosses that commit.

`make ingest` advances the cached incremental feeds. `make ingest-full` bootstraps npm from the supported paginated replication catalog, then enumerates the complete PyPI Simple index; both paths checkpoint progress and report every failed record. `make ingest-pypi-full` runs only the PyPI catalog path. Full ecosystem ingestion is network- and disk-bound, so the checked-in demo remains deliberately labeled as a measured partial corpus rather than a completeness claim.

Q8 can be exercised against an input that cannot be parsed: `make check-lockfile LOCKFILE=/path/to/bad/package-lock.json REPOSITORY=example/bad VALID_FROM=2026-08-13T00:00:00Z`. Blastline retains that real repository as unknown, records the failure, and prints the changed `M of N` coverage instead of treating it as clean.

Live HydraDB calls require `HYDRA_DB_API_KEY`; tenant and sub-tenant defaults are in [`config/default.json`](config/default.json). Without credentials, the exact local projection remains runnable and the live call says `ABSTAINED`.

## What is implemented

- Q1 reverse blast radius with repository paths and depth.
- Q2 advisory-backed first affected version.
- Q3 bitemporal window exposure, with historical/current comparison.
- Q4 maintainer credential blast radius.
- Q5 shared publisher/infrastructure relationships.
- Q6 explainable name-plus-topology proximity scoring.
- Q7 still-dirty candidates after the current resolution changes.
- Q8 explicit abstention and `M` of `N` repository coverage on every query.
- Hydra-backed candidate-path retrieval with typed temporal evidence validation and a measured local/Hydra agreement scorecard.
- Cached, resumable npm/PyPI/OSV/GitHub ingestion with idempotent graph writes and content-addressed fingerprints.
- Strict parsers for npm `package-lock.json`, Yarn, pnpm, Poetry, and pinned requirements files.

The `Resolution` node is deliberately first-class. A manifest range is not exposure: the same `^4.17.0` can resolve to a safe version, a compromised version, and a safe version again without the repository changing. Only a time-bounded resolution records those three states.

## Measured demo snapshot

The current local graph contains 878 packages, 1,641 versions, 8 maintainers, 2 publish-infrastructure records, 1 repository, 5,887 resolutions, 10 advisories, and 30,607 edges. It includes the real-response demo slice—one npm package (`lodash`), one PyPI package (`requests`), and five real lockfile snapshots from `npm/cli`—plus a partial real npm feed expansion. Eighty lockfile records lacked a resolved version and were reported as unknown; three PyPI releases had no file records. This is still a measured partial corpus, not an ecosystem-wide completeness claim; the resumable catalog paths are the route to broader coverage.

The generated incident artifact is [`examples/incident-report.json`](examples/incident-report.json). It includes the historical/current comparison, still-dirty candidates, coverage, graph fingerprint, local verification scorecard, and Hydra status or agreement scorecard. The measured graph report is [`examples/coverage-report.json`](examples/coverage-report.json).

## Documentation

- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) argues for the graph and the `Resolution` node.
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md) describes the ground-truth protocol and misses.
- [`docs/HYDRA.md`](docs/HYDRA.md) identifies the HydraDB primitives used and what would be lost without them.

## Attribution

Blastline uses the public [npm registry](https://github.com/npm/registry), the [npm replication changes feed](https://github.com/npm/replicate), the [PyPI JSON API](https://warehouse.pypa.io/api-reference/json.html), the [PyPI Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/), [OSV.dev](https://google.github.io/osv.dev/), and the [GitHub REST and raw-content APIs](https://docs.github.com/en/rest). Graph/context storage and graph-enriched recall use [HydraDB](https://docs.hydradb.com/api-reference). The implementation uses Python 3.11 standard-library modules and is licensed under Apache 2.0.
