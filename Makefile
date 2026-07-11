.PHONY: services services-down test check graph-test worker worker-prepare worker-extract worker-judge serve run ui-build ui-test

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
# that must never occupy a prepare/extract slot — see
# docs/superpowers/plans/2026-07-03-12-worker-isolation-and-synthesis-dedup.md.
# prepare (chunk_source_task) and extract (extract_chunk_task) used to share
# one "ingest" queue at prefork concurrency 2, sized for local Ollama.
# Extraction runs on OpenRouter (fast remote API) and wants much higher
# concurrency than the Whisper/memory-heavy prep step can safely run at, so
# they're split onto their own queues — see
# docs/superpowers/plans/2026-07-06-interactive-guide-and-throughput.md Phase 4.
# prepare concurrency defaults to 2 (safe for the memory-heavy Whisper/audio
# path); raise it via MSL_PREPARE_CONCURRENCY to prepare more sources at once
# on machines with embed headroom (e.g. OLLAMA_NUM_PARALLEL >= that many).
# Run all three (e.g. `make worker`, or `make run` for the full app).
worker-prepare:
	.venv/bin/celery -A mslearn.worker.app worker -Q prepare --concurrency=$${MSL_PREPARE_CONCURRENCY:-2} -n prepare@%h -l info

worker-extract:
	.venv/bin/celery -A mslearn.worker.app worker -Q extract --pool=threads --concurrency=$${MSL_EXTRACT_CONCURRENCY:-8} -n extract@%h -l info

worker-judge:
	.venv/bin/celery -A mslearn.worker.app worker -Q judge --concurrency=2 -n judge@%h -l info

worker: ## run all three ingest/judge workers (Ctrl-C stops all)
	$(MAKE) -j3 worker-prepare worker-extract worker-judge
