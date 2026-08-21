# How Blastline uses HydraDB

Blastline uses HydraDB as the graph/context substrate for the same typed records that drive the offline replay projection. This is an actual integration point, not a README-only dependency.

The temporal representation explicitly credits HydraDB’s graph-first architecture. Blastline specializes its bitemporal edge formulation, `e_k = (r_k, t_commit, t_valid, C_meta)`, for supply-chain evidence: typed relations become repository, resolution, version, advisory, maintainer, and publishing-infrastructure links; metadata carries deterministic source and parser provenance. HydraDB performs graph-context retrieval, while Blastline evaluates exact interval and knowledge-time predicates over the retrieved evidence.

## Primitives used

1. A dedicated HydraDB database and collection scope the Blastline graph. The values are configured in `config/default.json` and can be overridden by `HYDRA_DB_TENANT_ID` and `HYDRA_DB_SUB_TENANT_ID`.
2. The v2 `POST /context/ingest` endpoint receives deterministic `app_knowledge` source IDs for canonical node and edge records. Blastline sends them in configured batches with `upsert=true`, preserving node/edge identity across reruns.
3. Each record carries typed metadata under `additional_metadata.blastline_evidence`. HydraDB reserves fields such as `source_id`, so the edge fields are safely namespaced as `blastline_source_id` and normalized back to the Blastline schema only after receipt. No temporal field is inferred from prose.
4. The v2 `POST /query` endpoint is used in `hydra-window` with `graph_context=true`, thinking mode, and `query_forceful_relations=true` to discover candidate multi-hop paths. Blastline accepts both documented `query_paths` and `chunk_relations` graph-context paths. These ranked paths aid navigation but are not treated as an exhaustive security answer.
5. The v2 `POST /context/list` endpoint provides paginated exhaustive retrieval of the hosted typed records. It also powers the M0 read-back check proving that a write was accepted under its stable source ID. Database creation and readiness use `POST /databases` and `GET /databases/status` through `make hydra-init`.

`make publish-graph` explicitly re-upserts the current local graph using the current metadata schema. `make publish-flagship` publishes the deterministic connected evidence subgraph for the configured incident target, giving the live demo a bounded and reproducible source set. This is required after changing the evidence schema; `hydra-window` never assumes that an older hosted record has the fields needed for temporal verification.

`make publish-verification` similarly publishes the real OSV-backed lockfile cases used by `make hydra-verify`, so the Hydra/local scorecard has a declared, reproducible denominator.

The adapter is in [`src/blastline/hydra.py`](../src/blastline/hydra.py). Live failures raise loudly. When a key is absent, the CLI says `ABSTAINED`; it does not pretend the hosted graph was written.

## Where HydraDB does work

HydraDB supplies the multi-tenant graph/context boundary, durable typed records, candidate multi-hop path discovery, and exhaustive hosted record retrieval. `hydra-window` sends a deterministic exposure query containing the exact package/version, validity window, knowledge timestamp, and required Repository → Resolution → Version shape, then pages through the hosted collection so ranked recall cannot silently truncate the answer. Blastline maps Hydra source IDs to typed local edges and applies the exact `t_valid` interval and `t_commit` predicates before accepting a result.

This is Blastline’s central distinction from a present-day dependency lookup. For the committed flagship, the advisory-backed compromised version is `npm:lodash@4.17.21`. Blastline does not merely ask which repositories depend on Lodash now; it reconstructs which repositories resolved that exact compromised version during the requested exposure window, preserves the relationship path through HydraDB, and compares that historical set with the current set. A candidate path is evidence to verify, not a security answer to trust blindly.

This split is deliberate. The public HydraDB API exposes graph-context retrieval and source-level graph relation inspection, not an arbitrary temporal predicate language. Blastline therefore never treats recall text as proof. A candidate without a reconstructable typed edge is an abstention, and a candidate whose interval or knowledge timestamp fails validation is rejected.

Without HydraDB, Blastline would lose the hosted, tenant-isolated graph context, candidate multi-hop discovery, exhaustive hosted evidence retrieval, and durable graph evidence surface. The local store can still serve as an exact replay oracle for offline verification, but it would no longer demonstrate hosted graph discovery or the HydraDB-backed evidence path used by `hydra-window` and the timeline.

## Why a vector-only replacement is insufficient

HydraDB’s graph context can expose relationships between the canonical records. Blastline’s flagship answer additionally requires interval intersection and append-only knowledge time. A vector query cannot establish that `Resolution R` points to `Version V` during the requested window, nor can it return the exact repository path. The security claim is made only from typed edges and temporal predicates; similarity is a navigation aid, never evidence.

## Attribution

The adapter follows the public [HydraDB API reference](https://docs.hydradb.com/api-reference), [v2 SDK/API mapping](https://docs.hydradb.com/api-reference/v2/sdks), [v2 context-ingest contract](https://docs.hydradb.com/api-reference/v2/endpoint/ingest-context), [v2 query contract](https://docs.hydradb.com/api-reference/v2/endpoint/query), and [v2 relation-inspection contract](https://docs.hydradb.com/api-reference/v2/endpoint/source-relations). The project records request payloads through the same disk-cache rule used for the public registries.

The temporal-predicate gap and a backward-compatible request proposal are documented in [`RFC_HYDRA_TEMPORAL_PREDICATES.md`](RFC_HYDRA_TEMPORAL_PREDICATES.md) and filed upstream as [hydra-db/hydradb#111](https://github.com/hydra-db/hydradb/issues/111).
