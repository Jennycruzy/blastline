# Blastline data model

Blastline treats supply-chain exposure as a temporal graph problem. The graph is not a presentation of a relational table: its edge history is the evidence that a query is allowed to use.

## The node types

| Node | Why it is separate | What folding it away breaks |
| --- | --- | --- |
| `Package` | Stable identity across versions; name and registry questions attach here. | A version-only model cannot express reverse dependency closure or name proximity without rebuilding package identity from strings. |
| `Version` | The installable and potentially compromised unit. | `lodash` would be treated as malicious when only `lodash@4.17.21` is affected. |
| `Maintainer` | Credential ownership is a person/account relationship. | Package-only ownership loses the account blast radius and historical revocations. |
| `Repository` | The service/application a defender must remediate. | A package graph without an application endpoint answers the wrong operational question. |
| `Resolution` | A repository resolved one package to one version during one interval. | A dependency range would be mistaken for exposure; safe → malicious → safe resolution changes would collapse. |
| `Advisory` | An externally auditable disclosure event with a knowledge timestamp. | Maliciousness would be confused with publication or installation time. |
| `PublishInfra` | Registry/account/provenance grouping for shared-infrastructure questions. | Q5 would have to infer shared publishing from unrelated text. |

The edge vocabulary is `DEPENDS_ON`, `RESOLVED_TO`, `DECLARES`, `PUBLISHED_BY`, `MAINTAINS`, `PUBLISHED_FROM`, `AFFECTS`, and derived `SIMILAR_NAME_TO`. Derived similarity is never treated as an asserted dependency.

## Why `Resolution` is the decisive node

Suppose a service manifest says:

```text
"lodash": "^4.17.0"
```

That declaration is not an exposure record. On Monday the lockfile can resolve it to `4.17.21`; on Tuesday it can resolve it to a malicious `4.17.22`; on Wednesday it can resolve back to `4.17.21` after remediation. The repository file can remain unchanged for all three days.

Blastline therefore stores:

```text
Repository ─DECLARES→ Resolution ─RESOLVED_TO→ Version
```

with `Resolution.valid = [T1,T2)`. Q3 intersects that interval with the incident window. A graph that stores only `Repository —DEPENDS_ON→ Package` has no value for `T1`, `T2`, or the concrete version and cannot write the query at all.

## Bitemporal semantics

Every edge is an append-only record:

```text
e = (source, predicate, target, t_commit, [t_valid_start, t_valid_end), metadata)
```

` t_valid ` is world time. For a lockfile resolution, it is the interval during which that lockfile state was in force. For an advisory, it is the interval in which the affected version is considered malicious according to the advisory evidence. `t_commit` is knowledge time: the commit containing the lockfile, the registry observation, or the OSV disclosure.

Those axes can diverge. A package can be installable and malicious from 09:00, while OSV publishes the advisory at 09:20. A query with `valid_at=09:10, known_at=09:15` sees the installation but not the advisory. A query with `known_at=09:30` can use it. Nothing is overwritten; a maintainer revocation and a new grant are two edges, so “who could publish in May?” stays answerable in August.

The content-addressed graph fingerprint sorts canonical node and edge JSON and hashes it. It excludes ingestion timestamps. Replaying the same recorded response therefore produces the same fingerprint.

## Traversal versus SQL and vectors

The flagship traversal is:

```text
given Version V and window W
  ← RESOLVED_TO ← Resolution
  ← DECLARES ← Repository
  where Resolution.valid intersects W
  and Resolution.commit_at <= known_at
```

The equivalent relational shape needs at least a resolution table, an edge-history table, interval predicates, and joins over the package/version identity. A simplified query is:

```sql
SELECT DISTINCT r.repository_id
FROM resolution_edge re
JOIN declares_edge d ON d.resolution_id = re.resolution_id
JOIN resolution_edge target ON target.resolution_id = re.resolution_id
WHERE target.version_id = :compromised_version
  AND target.valid_start < :window_end
  AND (target.valid_end IS NULL OR target.valid_end > :window_start)
  AND target.commit_at <= :known_at;
```

That SQL can be made correct, but the application must maintain typed edge history, interval indexing, append-only updates, and the multi-hop reverse dependency closure beside it. The graph keeps the relationship path as the result and makes the next hop (`Version ← DEPENDS_ON ← Package`) the same operation. A vector index can retrieve text that mentions “lodash” but cannot prove an exact resolved version, interval intersection, or repository path; cosine similarity is not used for any security claim here.

The honest cost is storage and index maintenance. A graph traversal is proportional to the reachable subgraph, `O(V_reachable + E_reachable)` in the local replay projection, plus the cost of materializing typed edges. Q3 is a direct indexed reverse lookup over `RESOLVED_TO` and `DECLARES`; Q1 is a bounded breadth-first traversal with the configured depth cap; Q4 is maintainer-to-package followed by the same reverse closure; Q5 is two-hop shared-target lookup; Q6 scans candidate package names and then their graph statistics. The depth cap is a safety boundary: reaching it produces an abstention, never an invented completion.

## Ingestion invariants

Registry records and lockfile snapshots are cached before parsing. Node and edge IDs are deterministic. Re-ingestion skips an existing content identity. Parse failures are appended to the failure ledger with the source identifier and payload hash. A response that is absent is not represented as an empty dependency list.
