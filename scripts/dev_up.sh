#!/usr/bin/env bash
# One command to run the whole app: Redis + Neo4j containers, three
# dedicated Celery workers (prepare, extract, judge), and the API — trapped
# so a single Ctrl-C shuts everything down cleanly.
#
# `make serve` alone is NOT enough (see README "things must be running"):
# without the worker processes, uploaded sources sit in the queue forever
# and "Run synthesis" silently enqueues into the void. Three workers, not
# one consuming every queue, so a multi-minute synthesis run can never
# starve extraction of its worker slots, and the Whisper/memory-heavy
# prepare step can never starve extraction's high concurrency either. This
# script starts all of them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== starting Redis + Neo4j containers =="
docker compose up -d

wait_for() {
  local label="$1" check="$2" tries=60
  echo "== waiting for ${label} =="
  until .venv/bin/python -c "${check}" >/dev/null 2>&1; do
    tries=$((tries - 1))
    if [ "$tries" -le 0 ]; then
      echo "${label} did not become ready in time; check 'docker compose ps' / logs." >&2
      exit 1
    fi
    sleep 1
  done
  echo "${label} is up."
}

wait_for "Redis" "
import sys
import redis
from mslearn.settings import get_settings
redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1).ping()
"

wait_for "Neo4j" "
import sys
from neo4j import GraphDatabase
from mslearn.settings import get_settings
s = get_settings()
driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
driver.verify_connectivity()
driver.close()
"

PREPARE_WORKER_PID=""
EXTRACT_WORKER_PID=""
JUDGE_WORKER_PID=""
API_PID=""

cleanup() {
  echo
  echo "== shutting down workers + API (containers keep running; 'make services-down' to stop those) =="
  [ -n "$PREPARE_WORKER_PID" ] && kill "$PREPARE_WORKER_PID" 2>/dev/null || true
  [ -n "$EXTRACT_WORKER_PID" ] && kill "$EXTRACT_WORKER_PID" 2>/dev/null || true
  [ -n "$JUDGE_WORKER_PID" ] && kill "$JUDGE_WORKER_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  wait "$PREPARE_WORKER_PID" "$EXTRACT_WORKER_PID" "$JUDGE_WORKER_PID" "$API_PID" 2>/dev/null || true
}
trap cleanup INT TERM

# Dedicated per-queue workers, not one worker consuming everything: a
# synthesis (judge) run is a multi-minute reasoning task that must never
# occupy a prepare/extract slot (see plan 2026-07-03-12), and the
# Whisper/memory-heavy prepare step must stay at low prefork concurrency
# while extraction (pure remote OpenRouter I/O) scales far higher via a
# thread pool (see plan 2026-07-06 Phase 4). -l warning: task noise goes to
# ingest_sources.error / the failures endpoint, not the terminal; real
# problems still print.
echo "== starting Celery prepare worker =="
.venv/bin/celery -A mslearn.worker.app worker -Q prepare --concurrency="${MSL_PREPARE_CONCURRENCY:-8}" -n prepare@%h -l warning &
PREPARE_WORKER_PID=$!

echo "== starting Celery extract worker =="
.venv/bin/celery -A mslearn.worker.app worker -Q extract --pool=threads --concurrency="${MSL_EXTRACT_CONCURRENCY:-8}" -n extract@%h -l warning &
EXTRACT_WORKER_PID=$!

echo "== starting Celery judge (synthesis) worker =="
.venv/bin/celery -A mslearn.worker.app worker -Q judge --concurrency=2 -n judge@%h -l warning &
JUDGE_WORKER_PID=$!

DIST_INDEX="frontend/dist/index.html"
if [ ! -f "$DIST_INDEX" ]; then
  echo "== rebuilding frontend: frontend/dist is missing =="
  npm --prefix frontend run build
elif [ -n "$(find frontend/src -type f -newer "$DIST_INDEX" -print -quit)" ]; then
  echo "== rebuilding frontend: frontend/src has changes newer than the built dist bundle =="
  npm --prefix frontend run build
fi

echo "== starting API (uvicorn) =="
.venv/bin/uvicorn mslearn.server.app:create_app --factory --port 8000 \
  --log-level warning --no-access-log &
API_PID=$!

echo
echo "mslearn is up: http://localhost:8000 (Ctrl-C to stop the workers + API)"

wait "$PREPARE_WORKER_PID" "$EXTRACT_WORKER_PID" "$JUDGE_WORKER_PID" "$API_PID"
