#!/usr/bin/env bash
# One command to run the whole app: Redis + Neo4j containers, two dedicated
# Celery workers (ingest, judge), and the API — trapped so a single Ctrl-C
# shuts everything down cleanly.
#
# `make serve` alone is NOT enough (see README "four things must be
# running"): without the worker processes, uploaded sources sit in the queue
# forever and "Run synthesis" silently enqueues into the void. Two workers,
# not one consuming both queues, so a multi-minute synthesis run can never
# starve extraction of its worker slots. This script starts all four.
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

INGEST_WORKER_PID=""
JUDGE_WORKER_PID=""
API_PID=""

cleanup() {
  echo
  echo "== shutting down workers + API (containers keep running; 'make services-down' to stop those) =="
  [ -n "$INGEST_WORKER_PID" ] && kill "$INGEST_WORKER_PID" 2>/dev/null || true
  [ -n "$JUDGE_WORKER_PID" ] && kill "$JUDGE_WORKER_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  wait "$INGEST_WORKER_PID" "$JUDGE_WORKER_PID" "$API_PID" 2>/dev/null || true
}
trap cleanup INT TERM

# Dedicated per-queue workers, not one worker consuming both: a synthesis
# (judge) run is a multi-minute reasoning task that must never occupy an
# ingest slot, or it starves extraction for the whole run (see plan
# 2026-07-03-12). -l warning: task noise goes to ingest_sources.error / the
# failures endpoint, not the terminal; real problems still print.
echo "== starting Celery ingest worker =="
.venv/bin/celery -A mslearn.worker.app worker -Q ingest --concurrency=2 -n ingest@%h -l warning &
INGEST_WORKER_PID=$!

echo "== starting Celery judge (synthesis) worker =="
.venv/bin/celery -A mslearn.worker.app worker -Q judge --concurrency=1 -n judge@%h -l warning &
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

wait "$INGEST_WORKER_PID" "$JUDGE_WORKER_PID" "$API_PID"
