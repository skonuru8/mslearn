.PHONY: services services-down test check graph-test worker worker-judge serve run ui-build ui-test

serve:
	.venv/bin/uvicorn mslearn.server.app:create_app --factory --port 8000

run:
	bash scripts/dev_up.sh

ui-build:
	cd frontend && npm run build

ui-test:
	cd frontend && npm test

eval:
	.venv/bin/python -m mslearn.evals.run --offline

eval-live:
	.venv/bin/python -m mslearn.evals.run

release-check:
	bash scripts/release_check.sh

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
	docker compose --profile test up -d neo4j-test
	@until .venv/bin/python -c "from neo4j import GraphDatabase; d = GraphDatabase.driver('bolt://localhost:7690', auth=('neo4j','learnsys-test')); d.verify_connectivity(); d.close()" 2>/dev/null; do sleep 1; done
	MSL_TEST_NEO4J_URI=bolt://localhost:7690 .venv/bin/pytest -m neo4j -v
	docker compose --profile test rm -sf neo4j-test

# Dedicated per-queue workers: synthesis (judge) is a 10-minute reasoning run
# that must never occupy an ingest slot and starve extraction — see
# docs/superpowers/plans/2026-07-03-12-worker-isolation-and-synthesis-dedup.md.
# Run both (e.g. two terminals, or `make run`) for the full app.
worker:
	.venv/bin/celery -A mslearn.worker.app worker -Q ingest --concurrency=2 -n ingest@%h -l info

worker-judge:
	.venv/bin/celery -A mslearn.worker.app worker -Q judge --concurrency=1 -n judge@%h -l info
