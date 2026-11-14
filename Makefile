# VaxForge - stream A Makefile (README section 5).
# No `make` on Windows?  Every target is a one-liner around `python -m vaxforge ...` - run that directly.

PY        ?= python
PATHOGENS ?= Neisseria meningitidis,SARS-CoV-2
PATHOGEN  ?= Neisseria meningitidis
OUT       ?= runs/nmb
TOP_K     ?= 20

.PHONY: setup warm-cache run offline-run test verify doctor fixture docker-pull

setup:            ## python deps (+ Docker images when Docker is installed)
	$(PY) -m pip install -e ".[dev]"
	-docker pull ncbi/blast:2.16.0
	-docker pull brinkmanlab/psortb_commandline:1.0.2

docker-pull:      ## only the images
	docker pull ncbi/blast:2.16.0
	docker pull brinkmanlab/psortb_commandline:1.0.2

warm-cache:       ## prefetch S0+S1 data for the demo pathogens - run it the night before
	$(PY) -m vaxforge warm-cache --pathogens "$(PATHOGENS)"

run:              ## S0-S2 (and any later stage that is installed)
	$(PY) -m vaxforge run --pathogen "$(PATHOGEN)" --top-k $(TOP_K) --out $(OUT)

offline-run:      ## same, but never touch the network (cache only)
	$(PY) -m vaxforge run --pathogen "$(PATHOGEN)" --top-k $(TOP_K) --out $(OUT) --offline

verify:           ## check a run folder's digests, upstream chain and file hashes
	$(PY) -m vaxforge verify $(OUT)

doctor:           ## tools, cache and (with --online) network access
	$(PY) -m vaxforge doctor --online

test:
	$(PY) -m pytest

fixture:          ## rebuild fixtures/nmb_fixture from a finished run in runs/nmb
	$(PY) scripts/make_fixture.py
