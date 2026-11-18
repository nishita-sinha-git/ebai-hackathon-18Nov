# Single entry point for local runs and CI. Override the interpreter with PY=...
PY ?= python

.PHONY: lint typecheck security test build smoke-installed ci clean

lint:
	$(PY) -m ruff check app app.py tests eval scripts
	$(PY) -m ruff format --check app app.py tests eval

typecheck:
	$(PY) -m mypy

security:
	$(PY) -m bandit -q -r app -ll

test:
	$(PY) -m pytest --cov --cov-report=term-missing --cov-report=xml

build:
	rm -rf dist
	$(PY) -m pip wheel --no-deps -q -w dist .

smoke-installed: build
	PY=$(PY) bash scripts/smoke_installed.sh

ci: lint typecheck security test smoke-installed

clean:
	rm -rf dist build .coverage coverage.xml smoke-report.txt .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
