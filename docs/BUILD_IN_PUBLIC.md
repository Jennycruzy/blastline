# Blastline — Hack Hydra build-in-public drafts

Copy-ready drafts for two posts per day from August 17 through August 20, 2026.
The drafts use only capabilities and measurements currently present in the repository.
If a later checkpoint changes, update the number rather than making a stronger claim.

Recommended cadence: 10:00 and 18:00 WAT (UTC+1). Post to the build-in-public channel, then drop the public link in the Hack Hydra server.

Repository: https://github.com/Jennycruzy/blastline

## August 17 — project and first live Hydra path

### Morning

I’m building **Blastline** for Hack Hydra by @hydradb.

When a package is compromised, the useful question is not “is it bad now?” It is: **which repositories were exposed during the incident window, and are they clean today?**

Blastline turns real npm/PyPI and lockfile history into a dependency graph with versioned resolutions. The key object is a `Resolution`: a repository resolving a package to an exact version during an exact interval.

That makes blast radius a traversal and time-window problem—not a vector-search problem.

Shipping by Aug 20. Build log: https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

### Evening

Today’s HydraDB checkpoint: Blastline is now on HydraDB’s current v2 API path.

The repo can initialize a database, write typed graph records, wait for readiness, and read a stable source back. The demo graph is published as a real `Repository → Resolution → Version` subgraph.

That distinction matters: HydraDB is not just where ingestion happens. It is becoming the evidence surface the flagship query has to pass through.

Next checkpoint: make HydraDB return candidate paths, then validate their temporal evidence locally.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

## August 18 — HydraDB participates in the flagship query

### Morning

Blastline’s flagship query now asks HydraDB for graph evidence instead of treating the local graph as the final answer.

The query includes the exact package/version, requested validity window, knowledge timestamp, and required `Repository → Resolution → Version` shape.

HydraDB returns candidate paths and source IDs. Blastline reconstructs the typed records and checks `t_valid` and `t_commit` before accepting anything.

Recall is not proof. Missing or incomplete temporal evidence means rejection or abstention.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

### Evening

Live HydraDB result from Blastline:

`npm:write-file-atomic@7.0.1`

Historical window: `2026-07-08 18:00–20:30 UTC`

HydraDB returned 15 candidate paths. Blastline accepted 1 after temporal validation, rejected 4 candidate sources, and abstained on 0.

The accepted path points to `npm/cli` through:

`Repository → Resolution → Version`

Historical exposure: 1 repository.
Current exposure: 0 repositories.

That difference is the demo.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

## August 19 — verification and the Resolution node

### Morning

Blastline now has two scorecards instead of one:

1. local verification against real OSV-backed lockfile cases;
2. HydraDB candidate retrieval compared with the local temporal oracle.

Current recorded run:

- 15 gradable cases
- local precision: 1.0000
- local recall: 1.0000
- Hydra/local agreement: 15/15
- false confirmations: 0
- false omissions: 0
- Hydra abstentions: 0

This is a small measured corpus, not an ecosystem-wide accuracy claim. The misses and denominators are part of the artifact.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

### Evening

The strongest design decision in Blastline is a node many dependency tools skip: `Resolution`.

`lodash@4.17.0` is not an exposure claim. A repository’s resolved version, and the interval in which that resolution was in force, is.

So the graph stores:

`Repository → Resolution → Version`

with append-only `t_valid` and `t_commit` evidence. The same manifest range can resolve to a safe version, then a compromised version, then a safe version again without changing the manifest.

That is why this belongs in a temporal graph.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

## August 20 — submission-ready proof

### Morning

Blastline’s incident report is now generated from real runs, not hand-written demo data.

It records the target version, incident window, HydraDB candidate paths, accepted and rejected evidence, historical exposure, current exposure, still-dirty candidates, abstentions, graph fingerprint, verification scorecard, and commit SHA.

The command is:

`make report`

The point is reproducibility: a judge can inspect the evidence behind the answer instead of trusting a screenshot.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

### Evening

Submission checkpoint for Blastline.

HydraDB is visibly in the flagship path: it returns candidate relationship evidence, and Blastline performs the exact temporal verification before issuing an exposure result.

Current measured status:

- historical result differs from current result;
- Hydra/local agreement: 15/15 on the recorded verification corpus;
- local precision/recall: 1.0000 / 1.0000 on 15 gradable cases;
- measured package coverage: npm 0.020299%, PyPI 0.000804%;
- no ecosystem-wide completeness claim.

Small graph, real data, visible evidence, honest limits.

Shipping Blastline for Hack Hydra by @hydradb.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

## Latest differentiation update

Blastline is not just a dependency dashboard asking what depends on a vulnerable package today.

It reconstructs which repositories resolved the compromised version during the exposure window, follows the `Repository → Resolution → Version` path, and compares that historical result with the current state.

HydraDB returns the candidate relationship path. Blastline checks the typed edge evidence and exact temporal predicates before accepting it. If the path or timestamps cannot be verified, Blastline abstains instead of calling the repository clean.

That is the difference between a current dependency lookup and an auditable historical blast-radius query.

https://github.com/Jennycruzy/blastline

#HackHydra #HydraDB

## Posting guardrails

- Keep the repository link in every post so the build is auditable.
- Keep “15/15” qualified as Hydra/local agreement; it is not a claim about all repositories.
- Keep the measured npm/PyPI percentages; do not replace them with “full ecosystem.”
- If a live run abstains, publish the abstention and its reason. Do not turn it into a clean result.
- Do not paste the Hydra API key into a post, README, issue, screenshot, or commit.
