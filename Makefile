.PHONY: services services-down test check graph-test worker serve ui-build ui-test

serve:
	.venv/bin/uvicorn mslearn.server.app:create_app --factory --port 8000

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
	docker compose up -d neo4j && sleep 20 && .venv/bin/pytest -m neo4j -v

worker:
	.venv/bin/celery -A mslearn.worker.app worker -Q ingest,judge --concurrency=2 -l info
