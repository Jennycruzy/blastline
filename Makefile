PYTHON ?= python3
RUN = PYTHONPATH=src $(PYTHON) -m blastline.cli
REGISTRY ?= npm

.PHONY: hello hydra-init test ingest ingest-full ingest-pypi-full discover-corpus ingest-corpus prepare-graph publish-graph publish-flagship publish-verification measure-coverage demo demo-timetravel blast window hydra-window first-affected verify verify-check verify-holdout hydra-verify maintainer-risk shared-infra still-dirty typosquats coverage coverage-report timeline report check-lockfile

hello:
	$(RUN) hello

hydra-init:
	$(RUN) hydra-init

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

ingest:
	$(RUN) ingest

ingest-full:
	$(RUN) ingest --full

ingest-pypi-full:
	$(RUN) ingest --pypi-full

discover-corpus:
	$(RUN) discover-corpus

ingest-corpus:
	$(RUN) ingest --github-corpus

prepare-graph:
	@set -e; \
	if test -f cache/registry.tar.gz && test ! -f cache/registry/.archive-ready; then \
		echo "unpacking committed registry cache"; \
		tar -xzf cache/registry.tar.gz; \
		touch cache/registry/.archive-ready; \
	fi; \
	if test -s data/graph/nodes.jsonl && test -s data/graph/edges.jsonl; then \
		echo "offline graph projection already prepared"; \
	elif test -f data/graph.tar.zst; then \
		echo "unpacking committed offline graph snapshot"; \
		tar --zstd -xf data/graph.tar.zst; \
	else \
		echo "preparing offline graph projection from committed recordings"; \
		$(MAKE) --no-print-directory demo; \
		$(RUN) ingest --npm-package vs-deploy; \
		$(MAKE) --no-print-directory ingest-corpus; \
	fi; \
	PYTHONPATH=src $(PYTHON) -c 'import json; from pathlib import Path; from blastline.store import GraphStore; expected = json.loads(Path("examples/coverage-report.json").read_text())["graph_fingerprint"]; actual = GraphStore(Path("data/graph")).fingerprint(); print(f"offline graph fingerprint: {actual}"); assert actual == expected, f"graph fingerprint mismatch: expected {expected}, got {actual}"'; \
	touch data/graph/.corpus-ready

publish-graph:
	$(RUN) publish-graph

publish-flagship:
	$(RUN) publish-flagship

publish-verification:
	$(RUN) publish-verification

measure-coverage:
	$(RUN) measure-coverage

demo:
	$(RUN) ingest --npm-package lodash --pypi-package requests
	$(RUN) ingest --github-repository npm/cli --github-path package-lock.json --github-ref latest --github-ecosystem npm
	$(RUN) ingest --osv-package lodash --osv-registry npm

demo-timetravel blast window hydra-window first-affected verify hydra-verify maintainer-risk shared-infra still-dirty typosquats coverage coverage-report timeline report publish-graph publish-flagship publish-verification measure-coverage: prepare-graph

demo-timetravel:
	$(RUN) demo-timetravel

blast:
	$(RUN) blast --registry "$(REGISTRY)" --package "$(PKG)" --version "$(VERSION)"

window:
	$(RUN) window --registry "$(REGISTRY)" --package "$(PKG)" --version "$(VERSION)" --from "$(FROM)" --to "$(TO)"

hydra-window:
	$(RUN) hydra-window --registry "$(REGISTRY)" --package "$(PKG)" --version "$(VERSION)" --from "$(FROM)" --to "$(TO)"

first-affected:
	$(RUN) first-affected --registry "$(REGISTRY)" --package "$(PKG)" --version "$(VERSION)"

verify:
	$(RUN) verify

verify-check:
	$(RUN) verify --no-record

verify-holdout:
	$(RUN) verify-holdout

hydra-verify:
	$(RUN) hydra-verify

maintainer-risk:
	$(RUN) maintainer-risk --maintainer "$(MAINTAINER)"

shared-infra:
	$(RUN) shared-infra --registry "$(REGISTRY)" --package "$(PKG)" --version "$(VERSION)"

still-dirty:
	$(RUN) still-dirty --registry "$(REGISTRY)" --package "$(PKG)" --version "$(VERSION)" --from "$(FROM)" --to "$(TO)"

typosquats:
	$(RUN) typosquats --registry "$(REGISTRY)" --package "$(PKG)"

coverage:
	$(RUN) coverage

coverage-report:
	$(RUN) coverage-report

timeline:
	PYTHONPATH=src $(PYTHON) -m ui.server

report:
	$(RUN) report

check-lockfile:
	$(RUN) ingest --lockfile-path "$(LOCKFILE)" --lockfile-repository "$(REPOSITORY)" --lockfile-valid-from "$(VALID_FROM)"
