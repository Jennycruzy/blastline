PYTHON ?= python3
RUN = PYTHONPATH=src $(PYTHON) -m blastline.cli
REGISTRY ?= npm

.PHONY: hello test ingest ingest-full ingest-pypi-full demo demo-timetravel blast window hydra-window first-affected verify maintainer-risk shared-infra still-dirty typosquats coverage timeline report check-lockfile

hello:
	$(RUN) hello

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

ingest:
	$(RUN) ingest

ingest-full:
	$(RUN) ingest --full

ingest-pypi-full:
	$(RUN) ingest --pypi-full

demo:
	$(RUN) ingest --npm-package lodash --pypi-package requests
	$(RUN) ingest --github-repository npm/cli --github-path package-lock.json --github-ref latest --github-ecosystem npm
	$(RUN) ingest --osv-package lodash --osv-registry npm

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

timeline:
	PYTHONPATH=src $(PYTHON) -m ui.server

report:
	$(RUN) report

check-lockfile:
	$(RUN) ingest --lockfile-path "$(LOCKFILE)" --lockfile-repository "$(REPOSITORY)" --lockfile-valid-from "$(VALID_FROM)"
