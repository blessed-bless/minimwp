.PHONY: run test lint fmt format

run:
	uvicorn app.main:app --host 127.0.0.1 --port 8000

test:
	pytest -q

lint:
	ruff check app tests scripts

fmt format:
	ruff format app tests scripts
	ruff check --fix app tests scripts
