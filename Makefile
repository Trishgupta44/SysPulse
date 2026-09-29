# SysPulse build and run targets. Linux only (run inside WSL Ubuntu on Windows).
CC      ?= gcc
CFLAGS  ?= -O2 -g -Wall -Wextra -std=c11 -D_GNU_SOURCE
LDFLAGS ?=
LDLIBS  ?= -lsqlite3
PREFIX  ?= /usr/local
ROOT    := $(CURDIR)
VENV    ?= $(ROOT)/.venv
PY      := $(VENV)/bin/python
DB      ?= /var/lib/syspulse/metrics.db
RESULTS ?= results
BUILD   := build
USER_NAME := $(shell id -un)

.PHONY: help all build lint coverage test test-c test-py venv apt-deps run-collector inject evaluate demo dashboard \
        install-service uninstall-service docker-build docker-up docker-demo clean

help:
	@grep -E "^[a-z-]+:.*##" Makefile | sed -E "s/:.*## /\t/" | column -t -s "$$(printf '\t')"

all: build ## build the collector

apt-deps: ## install system packages (needs sudo)
	sudo apt-get update
	sudo apt-get install -y build-essential libsqlite3-dev sqlite3 stress-ng python3-venv python3-pip

build: $(BUILD)/syspulse-collector ## compile the C collector

$(BUILD)/syspulse-collector: collector/syspulse_collector.c collector/proc_parse.c collector/proc_parse.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ collector/syspulse_collector.c collector/proc_parse.c $(LDFLAGS) $(LDLIBS)

$(BUILD)/test_proc_parse: collector/test_proc_parse.c collector/proc_parse.c collector/proc_parse.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Wpedantic -o $@ collector/test_proc_parse.c collector/proc_parse.c -lm

test-c: $(BUILD)/test_proc_parse ## C unit tests (no sqlite needed)
	$(BUILD)/test_proc_parse tests/fixtures/proc

lint: venv ## ruff
	$(VENV)/bin/ruff check .

coverage: venv build ## pytest with a coverage report
	$(PY) -m pytest -q --cov

venv: $(VENV)/.stamp ## create the Python virtualenv
$(VENV)/.stamp: requirements.txt requirements-dev.txt
	python3 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements-dev.txt
	touch $@

test-py: venv build ## pytest (needs the collector binary for the integration tests)
	$(PY) -m pytest -q

test: test-c test-py ## run every test

run-collector: build ## run the collector in the foreground
	SYSPULSE_DB=$(DB) $(BUILD)/syspulse-collector --verbose

inject: venv ## run the fault-injection scenario and evaluate (about 25 minutes)
	SYSPULSE_DB=$(DB) SYSPULSE_PY=$(PY) scripts/inject_faults.sh

evaluate: venv ## score detectors on the latest ground-truth run
	$(PY) -m syspulse.evaluate --db $(DB) --out $(RESULTS)

demo: venv ## synthetic data plus evaluation, no root and no stress-ng needed
	$(PY) -m syspulse.demo --db $(ROOT)/demo.db --out $(RESULTS)

dashboard: venv ## start the Streamlit dashboard
	SYSPULSE_DB=$(DB) $(PY) -m streamlit run dashboard/app.py

install-service: build venv ## install and start the collector service and log-parser timer (needs sudo)
	sudo install -m 0755 $(BUILD)/syspulse-collector $(PREFIX)/bin/syspulse-collector
	for f in syspulse-collector.service syspulse-logparser.service syspulse-logparser.timer; do \
	  sed -e "s|@PREFIX@|$(PREFIX)|g" -e "s|@USER@|$(USER_NAME)|g" -e "s|@ROOT@|$(ROOT)|g" -e "s|@PYTHON@|$(PY)|g" \
	    systemd/$$f | sudo tee /etc/systemd/system/$$f >/dev/null; \
	done
	sudo systemctl daemon-reload
	sudo systemctl enable --now syspulse-collector.service syspulse-logparser.timer
	@echo "installed. check: systemctl status syspulse-collector"

uninstall-service: ## stop and remove the services (keeps the database)
	-sudo systemctl disable --now syspulse-collector.service syspulse-logparser.timer
	sudo rm -f /etc/systemd/system/syspulse-collector.service /etc/systemd/system/syspulse-logparser.service /etc/systemd/system/syspulse-logparser.timer $(PREFIX)/bin/syspulse-collector
	sudo systemctl daemon-reload

docker-build: ## build the container image
	docker build -t syspulse .

docker-up: ## live collector and dashboard in containers, http://localhost:8501
	docker compose up --build

docker-demo: ## synthetic-data dashboard in a container, http://localhost:8502
	docker compose --profile demo up --build demo

clean: ## remove build output and caches
	rm -rf $(BUILD) .pytest_cache demo.db demo.db-wal demo.db-shm $(RESULTS)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
