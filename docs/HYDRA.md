# How Blastline uses HydraDB

Blastline uses HydraDB as the graph/context substrate for the same typed records that drive the offline replay projection. This is an actual integration point, not a README-only dependency.

## Primitives used

1. A dedicated HydraDB tenant and sub-tenant scope the Blastline graph. The values are configured in `config/default.json` and can be overridden by `HYDRA_DB_TENANT_ID` and `HYDRA_DB_SUB_TENANT_ID`.
2. The documented `POST /memories/add_memory` endpoint receives deterministic, idempotent `source_id`s for canonical node and edge records. Blastline sends them in configured batches with `upsert=true`, preserving node/edge identity across reruns.
3. Each record carries typed metadata (`blastline_record_type`, node/edge type) so HydraDB’s context graph can connect the same package, version, repository, resolution, advisory, and infrastructure vocabulary.
4. The documented `POST /recall/full_recall` endpoint is available through the typed client with `graph_context=true` and `alpha=0.0` for graph/keyword-oriented inspection. This is useful for a judge exploring provenance, while the exact temporal query remains deterministic over the canonical projection.
5. The documented list endpoint is used by the M0 read-back check to prove that a write was accepted and can be retrieved by its stable source ID.

The adapter is in [`src/blastline/hydra.py`](../src/blastline/hydra.py). Live failures raise loudly. When a key is absent, the CLI says `ABSTAINED`; it does not pretend the hosted graph was written.

## Where HydraDB does work

HydraDB supplies the multi-tenant graph/context boundary, graph-enriched retrieval, and durable object-backed context surface. The canonical node/edge records are the same records that the local append-only store replays for exact time-travel and offline judging. This split is deliberate: the public HydraDB API exposes typed context ingestion and graph-context recall, while a security result must still be reproducible without a network connection or a semantic reranker.

Without HydraDB, Blastline would lose the hosted, tenant-isolated graph context and the graph-enriched provenance surface. The local store could still perform a small replay, but it would no longer demonstrate the scale-oriented graph/context substrate, batched durable ingestion, or hosted graph inspection that the project is built to exercise.

## Why a vector-only replacement is insufficient

HydraDB’s graph context can expose relationships between the canonical records. Blastline’s flagship answer additionally requires interval intersection and append-only knowledge time. A vector query cannot establish that `Resolution R` points to `Version V` during the requested window, nor can it return the exact repository path. The security claim is made only from typed edges and temporal predicates; similarity is a navigation aid, never evidence.

## Attribution

The adapter follows the public [HydraDB API reference](https://docs.hydradb.com/api-reference), [memory ingestion contract](https://docs.hydradb.com/api-reference/endpoint/add-memory), and [graph-context recall contract](https://docs.hydradb.com/api-reference/endpoint/full-recall). The project records request payloads through the same disk-cache rule used for the public registries.
