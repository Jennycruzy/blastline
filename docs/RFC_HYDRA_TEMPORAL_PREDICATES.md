# RFC: temporal predicates for HydraDB graph-context retrieval

Status: draft for upstream discussion

Upstream discussion: [hydra-db/hydradb#111](https://github.com/hydra-db/hydradb/issues/111)

## Summary

HydraDB graph-context retrieval can return candidate entity paths and source-level relations. Supply-chain incident response additionally needs deterministic predicates over when an edge was valid and when the system learned it. This RFC proposes optional temporal filters for graph relations so HydraDB can prune impossible paths before returning graph context.

## Use case

Blastline stores evidence equivalent to:

```text
e_k = (r_k, t_commit, t_valid, C_meta)
```

- `r_k`: a typed relation such as `Repository → Resolution → Version`.
- `t_commit`: the knowledge timestamp.
- `t_valid`: a half-open validity interval `[start, end)`.
- `C_meta`: source identity, parser evidence, and lockfile provenance.

For an incident window `[W1,W2)` and a knowledge cutoff `K`, a path is admissible only when every required edge satisfies:

```text
edge.t_commit <= K
edge.t_valid.start < W2
edge.t_valid.end is null or edge.t_valid.end > W1
```

Metadata filtering can scope sources, but the public graph-context query surface does not currently expose these interval predicates over returned relations. Blastline therefore retrieves candidate paths from HydraDB and verifies the temporal evidence locally. Invalid candidates are rejected; missing typed evidence causes an abstention.

## Proposed request shape

Add an optional `relation_filters.temporal` object to graph-context queries:

```json
{
  "graph_context": true,
  "relation_filters": {
    "temporal": {
      "commit_at": {"lte": "2026-04-01T23:50:27Z"},
      "valid": {
        "intersects": {
          "start": "2021-02-20T15:42:16Z",
          "end": "2026-08-01T00:00:00Z"
        }
      }
    }
  }
}
```

The filter is optional and backward compatible. When omitted, graph retrieval behaves as it does today.

## Response requirements

For accepted relations, the response should preserve:

- stable relation and source IDs;
- the typed predicate;
- normalized `commit_at`, `valid.start`, and nullable `valid.end` values;
- enough source metadata to inspect or fetch the original evidence.

Malformed temporal metadata should be reported separately from a valid query that returns no matching paths. This distinction lets security applications abstain instead of interpreting missing evidence as no exposure.

## Why this belongs in retrieval

Server-side pruning reduces irrelevant candidate paths, lowers response size, and makes temporal graph applications easier to implement consistently. Local verification should still remain available for high-stakes claims, but it becomes a defense-in-depth check instead of the only place interval semantics can be expressed.

## References

- [HydraDB architecture](https://docs.hydradb.com/essentials/architecture)
- [HydraDB metadata](https://docs.hydradb.com/essentials/metadata)
- [HydraDB API reference](https://docs.hydradb.com/api-reference)
- [Blastline HydraDB integration](HYDRA.md)
- [Blastline data model](DATA_MODEL.md)
