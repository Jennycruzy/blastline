# Real GitHub corpus selection

Blastline does not mint repositories or sample synthetic lockfiles. `make discover-corpus` starts from package identities attached to real OSV `AFFECTS` edges in the current graph, then runs authenticated GitHub code search for those names inside supported npm and PyPI lockfile formats.

The selection is deterministic once the responses are cached:

1. Search results are cached before parsing and keyed by the request URL.
2. A repository/path candidate is retained only when GitHub reports at least the configured minimum number of distinct commits touching that lockfile.
3. Candidates are sorted by owner, repository, path, and ecosystem. The configured owner cap is applied mechanically.
4. Candidates are selected without looking at whether the affected version appears. That preserves negative cases for verification instead of tuning the corpus toward exposure.
5. The selected repository, lockfile path, branch, commit SHAs, and commit timestamps are written to [`cache/corpus/github-lockfiles.json`](../cache/corpus/github-lockfiles.json). The manifest is tied to the graph fingerprint that supplied its advisory package seeds.

The ingestion pass uses the recorded commit timestamps to create each snapshot's validity interval: a snapshot begins at its commit time and ends at the next lockfile-touching commit. Raw lockfile responses are cached and recorded in the verification snapshot ledger before parsing. A failed repository remains in the manifest and is written to the append-only failure ledger; it is never reported as having no exposure.

The selection parameters live in [`config/default.json`](../config/default.json) under `corpus`. Discovery requires `GITHUB_TOKEN` or `GH_TOKEN` because GitHub code search and commit-history calls must be authenticated. Once the manifest and raw responses are committed, replaying the selected corpus does not need credentials for discovery or commit-history lookup.

The current graph may not contain advisory seeds for both ecosystems. Blastline reports that absence rather than inventing PyPI candidates; broader OSV ingestion is required before a two-ecosystem corpus can be claimed.
