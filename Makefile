.PHONY: services services-down test check graph-test

services:
	docker compose up -d

services-down:
	docker compose down

test:
	.venv/bin/pytest

check:
	.venv/bin/ruff check .
	.venv/bin/pytest

graph-test:
	docker compose up -d neo4j && sleep 20 && .venv/bin/pytest -m neo4j -v
