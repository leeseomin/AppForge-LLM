.PHONY: install frontend-install frontend-build frontend-dev run test check build clean

install: frontend-install
	python -m pip install -e '.[dev]'

frontend-install:
	npm --prefix frontend install

frontend-build:
	npm --prefix frontend run build

frontend-dev:
	npm --prefix frontend run dev

run:
	python -m appforge web

test:
	python -m pytest

check: frontend-build
	python -m compileall -q appforge tests
	python -m appforge pipelines >/dev/null
	python -m appforge tool list >/dev/null
	python -m appforge web --help >/dev/null
	python -m pytest

build: check
	python -m pip wheel . --no-deps --no-build-isolation -w dist

clean:
	rm -rf build dist .pytest_cache .ruff_cache *.egg-info frontend/node_modules
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
