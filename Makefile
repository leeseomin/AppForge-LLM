.PHONY: install test check build clean

install:
	python -m pip install -e '.[dev]'

test:
	python -m pytest

check:
	python -m compileall -q appforge tests
	python -m appforge pipelines >/dev/null
	python -m appforge tool list >/dev/null
	python -m pytest

build: check
	python -m build

clean:
	rm -rf build dist .pytest_cache .ruff_cache *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
