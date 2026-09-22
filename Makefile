PYTHON ?= python

.PHONY: help setup test lint format build preview chat evaluate evaluate-business api check-api check-security audit-dependencies
help:
	@echo "setup | test | lint | format | build | preview | chat | evaluate | evaluate-business | api | check-api | check-security | audit-dependencies"

setup:
	$(PYTHON) src/react_agent/data/init_db.py
	$(PYTHON) src/react_agent/data/init_returns.py
	$(PYTHON) src/react_agent/data/init_shipments.py
	$(PYTHON) src/react_agent/data/init_return_requests.py

test:
	$(PYTHON) -m pytest -q tests/unit_tests

lint:
	$(PYTHON) -m ruff check src scripts
	$(PYTHON) -m ruff format --check src scripts

format:
	$(PYTHON) -m ruff check --fix src scripts
	$(PYTHON) -m ruff format src scripts

build:
	$(PYTHON) -m build --wheel

preview:
	$(PYTHON) scripts/build_knowledge.py --preview

chat:
	$(PYTHON) scripts/run_local.py

evaluate:
	$(PYTHON) scripts/evaluation/evaluate_retrieval.py

evaluate-business:
	$(PYTHON) -u scripts/evaluation/evaluate_business.py

api:
	$(PYTHON) scripts/run_api.py

check-api:
	$(PYTHON) scripts/maintenance/check_api.py

check-security:
	$(PYTHON) scripts/evaluation/evaluate_security.py --output knowledge/results/security_boundary_evaluation.json

audit-dependencies:
	$(PYTHON) scripts/maintenance/audit_dependencies.py --osv
