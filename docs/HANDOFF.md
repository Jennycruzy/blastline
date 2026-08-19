# Blastline handoff

## Current state

The project baseline is `0b84f31` (`Fix pnpm lockfile parsing and corpus ingest performance`). The working tree contains the regenerated real GitHub corpus inputs, source fixes, refreshed reports, and compressed registry-cache/graph snapshots. The expanded loose `data/graph/` projection and `cache/registry/` cache are ignored because GitHub rejects the loose graph edge file and the cache would make the push unnecessarily large; `make prepare-graph` unpacks and verifies both archives.

## Completed

- Fixed pnpm parsing for legacy `/package/version`, hybrid `/package@version`, scoped, quoted, and peer-context keys.
- Stripped quotes from pnpm dependency names before graphification.
- Deferred GitHub corpus graph fingerprinting until the full `make ingest-corpus` pass completes.
- Added snapshot interval validation so invalid `valid_to <= committed_at` records cannot enter the verification ledger.
- Avoided repeated full graph-list copies during node and edge lookups, which keeps verification usable on the expanded corpus.
- Added focused regression coverage for pnpm parsing and invalid snapshot intervals.
- Rebuilt the corpus from a fresh committed baseline: 53 repositories selected, 327 real snapshots parsed, 199,089 resolutions, 10 failed records, and 0 failed repositories.

## Validation

```text
22 tests, OK
verification: 50 gradable cases; TP=877; FP=354; FN=0
precision 0.7124; recall 1.0000
graph fingerprint: b5fa455a5d3eea7bb0cda7e6a882e1ecccdb1fa16631889e16e711126a346619
```

The rebuilt verification ledger contains 332 valid snapshots. No pnpm package-key errors or quoted package identities remain in the regenerated projection. Coverage and incident artifacts are refreshed in `examples/coverage-report.json` and `examples/incident-report.json`. Hydra-backed checks remain `ABSTAINED` because `HYDRA_DB_API_KEY` is not set.

## Rebuild path

From a fresh clone, run `make prepare-graph`. It unpacks and verifies the compressed cache and graph snapshots, or uses the committed recordings to restore the demo seed and cached `vs-deploy` baseline before ingesting the selected corpus when the snapshots are absent. The target is also a prerequisite of graph-consuming Make commands, including `make verify` and `make report`.
