#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== offline pytest =="
.venv/bin/pytest -q

echo "== ruff =="
.venv/bin/ruff check .

echo "== optional neo4j =="
if .venv/bin/pytest -m neo4j -q --collect-only 2>/dev/null | rg -q "test session starts"; then
  docker compose up -d neo4j >/dev/null 2>&1 || true
  sleep 5
  .venv/bin/pytest -m neo4j -q || echo "neo4j tests skipped/failed (services may be down)"
fi

echo "== eval runner (offline) =="
.venv/bin/python -m mslearn.evals.run --offline || true

echo "release_check complete"
