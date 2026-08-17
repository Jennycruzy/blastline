# Verification protocol

Blastline treats “these repositories are exposed” as a falsifiable claim.

## Protocol

1. Select a real public advisory from OSV.dev and a real public repository with a committed lockfile.
2. Ingest registry history, advisory disclosure data, and the repository’s lockfile commits. The GitHub adapter parses each committed snapshot instead of treating a manifest range as a resolved version.
3. Predict exposure with Q1/Q3 from the graph.
4. Resolve the same lockfile snapshots through the strict parser and use the resulting `Resolution` records as observed ground truth. Records that have no resolved version are retained in the failure ledger and are not silently counted as clean.
5. Compare predicted and observed repository sets. False negatives are printed before aggregate metrics.
6. Append the scorecard, graph fingerprint, and Git commit SHA to `cache/verification/runs.jsonl`.

The verification target is not merely “does this repository depend on the package today?” Blastline must reconstruct the repository’s historical resolution during the requested exposure window, preserve the `Repository → Resolution → Version` evidence path, and distinguish that result from the current state. A missing path, missing timestamp, or unresolved lockfile produces an explicit abstention rather than a clean verdict.

The verifier does not tune thresholds to a perfect number. It reports the denominator and excludes ungradable cases from precision/recall. Its case-level `abstentions` field preserves the reason a case was not gradable.

## Current recorded run

The current committed demo corpus contains one public GitHub repository (`npm/cli`), five real lockfile snapshots, three OSV-backed advisory/version relationships, and 15 gradable verification cases. The latest scorecard records:

```text
TP=15  FP=0  FN=0
15 gradable, 0 ungradable and excluded
precision=1.0000  recall=1.0000
```

This is evidence about the recorded corpus, not a claim that a 15-case sample proves ecosystem-wide correctness. The run is intentionally accompanied by the raw failure ledger: three real PyPI releases had no file records in the public response, and 80 real lockfile records had no resolved version. Those are misses in coverage, not fabricated “safe” outcomes.

## Hydra-backed agreement

`make hydra-verify` runs the same discovered cases through HydraDB candidate-path retrieval and compares the temporally verified Hydra result with the local oracle. It records candidate-path counts, rejected source records, abstentions, latency, disagreements, false confirmations, false omissions, graph fingerprint, and commit SHA in `cache/verification/hydra-agreement.jsonl`.

The command requires `HYDRA_DB_API_KEY`. When the key is absent, it exits with an explicit abstention and records no invented score. A Hydra agreement score is not inferred from the local 15-case score.

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
make demo
make verify
```

`make demo` uses only committed recordings after the first capture. To capture a fresh response, run `PYTHONPATH=src python3 scripts/record_real_responses.py ...`; the script uses public sources and writes the exact response bytes, never a synthetic fixture.
