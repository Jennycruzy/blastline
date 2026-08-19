# Verification protocol

Blastline treats “these repositories are exposed” as a falsifiable claim.

## Protocol

1. Select affected package versions from real public OSV.dev advisories.
2. Discover positive cases by parsing the raw snapshot ledger directly; graph `RESOLVED_TO` edges are not used to choose cases.
3. Ingest registry history, advisory disclosure data, and repository lockfile commits. The GitHub adapter parses each committed snapshot instead of treating a manifest range as a resolved version.
4. Predict exposure with Q1/Q3 from the graph.
5. Resolve the same raw lockfile snapshots through the strict parser again, using the graph-free cached-lockfile oracle. Records that have no resolved version are retained in the failure ledger and are not silently counted as no exposure.
6. Compare predicted and observed repository sets. False negatives are printed before aggregate metrics.
7. Append the scorecard, graph fingerprint, and Git commit SHA to `cache/verification/runs.jsonl` unless `--no-record` is used for CI.

The verification target is not merely “does this repository depend on the package today?” Blastline must reconstruct the repository’s historical resolution during the requested exposure window, preserve the `Repository → Resolution → Version` evidence path, and distinguish that result from the current state. A missing path, missing timestamp, or unresolved lockfile produces an explicit abstention rather than an unsupported no-exposure verdict.

The verifier does not tune thresholds to a perfect number. It reports temporal cases separately from positive `(case, repository)` decisions and excludes ungradable cases from precision/recall. True negatives are not enumerated. Its case-level `abstentions` field preserves the reason a case was not gradable.

## Independent observation oracle

GitHub ingestion records each fetched lockfile snapshot in [`cache/verification/lockfile-snapshots.jsonl`](../cache/verification/lockfile-snapshots.jsonl), including its repository, commit, validity interval, raw URL, and payload hash. Verification reads the corresponding raw response from the committed HTTP cache, validates the hash, and reparses the lockfile without consulting graph `Resolution`, `DECLARES`, or `RESOLVED_TO` edges for the observed set.

This separates two failure classes: the graph query can disagree with the lockfile observation, and the lockfile observation can abstain when its raw evidence is missing, corrupt, or unparseable. The strict parser is shared with ingestion, but graph projection is not part of the observation path.

## Current check

The current corpus contains 53 selected public GitHub repositories, 54 repositories in the graph, 332 valid raw lockfile snapshots, 10 advisories, and 50 gradable ledger-derived verification cases. The current check records:

```text
TP=337  FP=103  FN=1
441 positive repository-pair decisions; true negatives not enumerated
50 gradable, 0 ungradable and excluded
precision=0.7659  recall=0.9970
```

This is evidence about the recorded corpus, not a claim that a 50-case sample proves ecosystem-wide correctness. The observed false negative is published rather than tuned away. The run is intentionally accompanied by the append-only failure ledger; the latest corpus pass added 10 explicit failures and no failed repositories. Those are misses in coverage, not fabricated “safe” outcomes.

## Manually reviewed parser holdout

`make verify-holdout` checks immutable raw payloads against four human-reviewable labels in [`cache/verification/manual-lockfile-holdout.json`](../cache/verification/manual-lockfile-holdout.json): package-lock and pnpm positives for `lodash@4.17.21`, a pnpm lockfile containing `lodash@4.18.1` instead, and a pnpm lockfile containing only a different `lodash.*` package. Each label includes the raw URL, payload hash, and the exact evidence that was reviewed before parser execution. This small holdout tests parser behavior independently of graph projection; it is intentionally not presented as broad parser accuracy.

## Hydra-backed agreement

`make hydra-verify` runs a bounded 10-case slice through HydraDB graph-context retrieval plus exhaustive paginated hosted-record discovery and compares the temporally verified result with the local oracle. The current recorded run agrees on 10/10 cases with zero false confirmations, zero omissions, and zero abstentions. It records candidate-path counts, rejected source records, abstentions, latency, disagreements, graph fingerprint, and commit SHA in `cache/verification/hydra-agreement.jsonl`.

The command requires `HYDRA_DB_API_KEY`. When the key is absent, it exits with an explicit abstention and records no invented score. A Hydra agreement score is not inferred from the local 50-case score.

## Measured ingestion coverage

`make measure-coverage` records package-name denominators from the authoritative npm replication catalog and PyPI Simple index. `make coverage-report` then writes [`examples/coverage-report.json`](../examples/coverage-report.json) with observed packages, versions, maintainers, graph counts, denominator sources, measured percentages, and failure-ledger counts. If a source does not publish an authoritative denominator, coverage is reported as `not-measured` rather than estimated.

## Confusion matrix definition

The unit is a `(verification case, repository)` pair.

- True positive: predicted and observed exposure.
- False negative: observed exposure absent from the prediction; this is the first and most prominent error.
- False positive: predicted exposure absent from the parsed lockfile ground truth.
- Ungradable: the lockfile, advisory, or required timestamp could not be parsed; it is excluded from the metric and listed as an abstention.

```text
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
```

An undefined denominator is printed as `not-defined`, never as zero. The same rule applies to Q6 component scores when the registry does not publish the required account timestamp.

## Reproducing it

```sh
make prepare-graph
make verify
make verify-holdout
```

`make prepare-graph` uses only committed material after the first capture. It unpacks the compressed registry cache and graph snapshot when available; if the snapshot is absent, it rebuilds the ignored graph projection from the recorded seed, including the cached `vs-deploy` baseline required by the corpus manifest, and then replays the manifest. It does not require GitHub credentials or live network access. The resulting fingerprint is checked into the coverage and incident artifacts, making an accidental or incomplete rebuild visible. To capture a fresh response, run `PYTHONPATH=src python3 scripts/record_real_responses.py ...`; the script uses public sources and writes the exact response bytes, never a synthetic fixture.
