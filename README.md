# BLASTLINE

When a package is compromised at 09:00, Blastline answers which real services were exposed by 09:06, which were exposed only during the incident window, and which are still dirty after their current lockfile looks clean.

The enabling primitive is a bitemporal dependency graph. `t_valid` is when a resolution or malicious version was true in the world. `t_commit` is when Blastline learned it. Keeping those axes separate makes “who installed it while nobody knew?” a traversal instead of a guess.

## Verification first

The committed scorecard is generated from real OSV advisory records and real public GitHub lockfile history. The latest recorded run covers 15 gradable cases: TP=15, FP=0, FN=0, precision 1.0000, recall 1.0000. This is a small measured corpus, not an ecosystem-wide accuracy claim. Every run records its graph fingerprint, commit SHA, denominator, abstentions, and misses in [`cache/verification/runs.jsonl`](cache/verification/runs.jsonl). Current-source failures remain itemized in [`cache/ingest-failures.jsonl`](cache/ingest-failures.jsonl).

## Run it

The repository has no third-party runtime dependency. The first smoke check is local and offline:

```sh
make hello
make test
```

Rebuild the committed offline demo graph from recorded real registry responses:

```sh
make demo
make window REGISTRY=npm PKG=write-file-atomic VERSION=7.0.1 \
  FROM=2026-07-08T18:00:00Z TO=2026-07-08T20:30:00Z
make verify
make report
```

The window command prints the live temporal query, its paths and latency-independent evidence, then compares the historical set with the present-day set. In the committed demo, the historical set contains `npm/cli` and the present-day set is empty. The timeline starts before the real `npm/cli` resolution commit, so its exposed set grows from zero to one as the scrubber crosses that commit.

`make ingest` advances the cached incremental feeds. `make ingest-full` drains npm changes from its checkpoint and enumerates the complete PyPI Simple index; it is resumable, network-bound, and reports every failed record. The checked-in demo is deliberately a measured partial corpus rather than a completeness claim.

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
- Cached, resumable npm/PyPI/OSV/GitHub ingestion with idempotent graph writes and content-addressed fingerprints.
- Strict parsers for npm `package-lock.json`, Yarn, pnpm, Poetry, and pinned requirements files.

The `Resolution` node is deliberately first-class. A manifest range is not exposure: the same `^4.17.0` can resolve to a safe version, a compromised version, and a safe version again without the repository changing. Only a time-bounded resolution records those three states.

## Measured demo snapshot

The committed real-response recordings produce 844 packages, 1,296 versions, 7 maintainers, 2 publish-infrastructure records, 1 repository, 5,887 resolutions, 3 advisories, and 22,951 edges. The source slice is one npm package (`lodash`, 117 versions), one PyPI package (`requests`, 160 versions), and five real lockfile snapshots from `npm/cli`. Eighty lockfile records lacked a resolved version and were reported as unknown; three PyPI releases had no file records. The snapshot is intentionally labeled as a partial corpus; the resumable feeds are the path to broader coverage.

The generated incident artifact is [`examples/incident-report.json`](examples/incident-report.json). It includes the historical/current comparison, still-dirty candidates, coverage, graph fingerprint, and verification scorecard.

## Documentation

- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) argues for the graph and the `Resolution` node.
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md) describes the ground-truth protocol and misses.
- [`docs/HYDRA.md`](docs/HYDRA.md) identifies the HydraDB primitives used and what would be lost without them.

## Attribution

Blastline uses the public [npm registry](https://github.com/npm/registry), the [npm replication changes feed](https://github.com/npm/replicate), the [PyPI JSON API](https://warehouse.pypa.io/api-reference/json.html), the [PyPI Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/), [OSV.dev](https://google.github.io/osv.dev/), and the [GitHub REST and raw-content APIs](https://docs.github.com/en/rest). Graph/context storage and graph-enriched recall use [HydraDB](https://docs.hydradb.com/api-reference). The implementation uses Python 3.11 standard-library modules and is licensed under Apache 2.0.
