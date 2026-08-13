# BLASTLINE

Blastline answers one incident-response question: when a package was compromised, which real repositories were exposed during the interval, and which remain dirty after their current lockfile is clean?

The enabling primitive is a bitemporal dependency graph. `t_valid` says when a resolution or malicious version was true in the world; `t_commit` says when Blastline learned it. Those axes stay separate, so a historical exposure query can differ from today's answer.

The repository is being built from a dated greenfield scaffold for Hack Hydra, Track 02A. Commands and measured results will be added incrementally as each milestone passes.

## Quick start

```sh
make hello
make test
```

Live HydraDB runs require the credentials documented in the ingestion milestone. Offline tests use only recorded real responses; no synthetic dependency graph is included.

## Status

The first commit is the small scaffold. The bitemporal write path and registry adapters are the next milestones.

## Attribution

Planned external sources are the npm registry, PyPI, OSV.dev, and public GitHub lockfiles. The runtime will record exact request URLs, response hashes, and library versions in generated provenance files. HydraDB is the graph and temporal context service used by Blastline.
