.PHONY: test lint run keepalive install

install:
	pip install -e ".[dev]"

test:
	pytest --cov=. --cov-report=term-missing

lint:
	ruff check .
	black --check .

format:
	ruff check --fix .
	black .

run:
	python scripts/run_morning.py

keepalive:
	python scripts/keepalive_only.py
