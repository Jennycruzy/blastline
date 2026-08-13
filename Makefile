PYTHON ?= python3
RUN = PYTHONPATH=src $(PYTHON) -m blastline.cli

.PHONY: hello test ingest demo-timetravel blast window verify maintainer-risk typosquats timeline report

hello:
	$(RUN) hello

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

ingest:
	$(RUN) ingest

demo-timetravel:
	$(RUN) demo-timetravel

blast:
	$(RUN) blast --package "$(PKG)" --version "$(VERSION)"

window:
	$(RUN) window --from "$(FROM)" --to "$(TO)"

verify:
	$(RUN) verify

maintainer-risk:
	$(RUN) maintainer-risk --maintainer "$(MAINTAINER)"

typosquats:
	$(RUN) typosquats --package "$(PKG)"

timeline:
	$(RUN) timeline

report:
	$(RUN) report
