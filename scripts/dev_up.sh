#!/usr/bin/env bash
# One command to run the whole app: Redis + Neo4j containers, the Celery
# worker, and the API — trapped so a single Ctrl-C shuts everything down
# cleanly.
#
# `make serve` alone is NOT enough (see README "three things must be
# running"): without the worker process, uploaded sources sit in the queue
# forever and "Run synthesis" silently enqueues into the void. This script
# starts all three.
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

WORKER_PID=""
API_PID=""

cleanup() {
  echo
  echo "== shutting down worker + API (containers keep running; 'make services-down' to stop those) =="
  [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  wait "$WORKER_PID" "$API_PID" 2>/dev/null || true
}
trap cleanup INT TERM

echo "== starting Celery worker =="
# -l warning: task noise goes to ingest_sources.error / the failures
# endpoint, not the terminal; real problems still print.
.venv/bin/celery -A mslearn.worker.app worker -Q ingest,judge --concurrency=2 -l warning &
WORKER_PID=$!

echo "== starting API (uvicorn) =="
.venv/bin/uvicorn mslearn.server.app:create_app --factory --port 8000 \
  --log-level warning --no-access-log &
API_PID=$!

echo
echo "mslearn is up: http://localhost:8000 (Ctrl-C to stop the worker + API)"

wait "$WORKER_PID" "$API_PID"
