PYTHON ?= python3

.PHONY: hello test ingest demo-timetravel blast window verify maintainer-risk typosquats timeline report

hello:
	$(PYTHON) -m blastline.cli hello

test:
	$(PYTHON) -m unittest discover -s tests -v

ingest:
	$(PYTHON) -m blastline.cli ingest

demo-timetravel:
	$(PYTHON) -m blastline.cli demo-timetravel

blast:
	$(PYTHON) -m blastline.cli blast --package "$(PKG)" --version "$(VERSION)"

window:
	$(PYTHON) -m blastline.cli window --from "$(FROM)" --to "$(TO)"

verify:
	$(PYTHON) -m blastline.cli verify

maintainer-risk:
	$(PYTHON) -m blastline.cli maintainer-risk --maintainer "$(MAINTAINER)"

typosquats:
	$(PYTHON) -m blastline.cli typosquats --package "$(PKG)"

timeline:
	$(PYTHON) -m blastline.cli timeline

report:
	$(PYTHON) -m blastline.cli report
