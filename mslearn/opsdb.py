import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms REAL,
    cost_usd REAL,
    outcome TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tunables (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tunable_audit (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    key TEXT NOT NULL,
    value REAL NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_sources (
    source_id TEXT PRIMARY KEY,
    ref TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    total_chunks INTEGER NOT NULL,
    done_chunks INTEGER NOT NULL DEFAULT 0,
    failed_chunks INTEGER NOT NULL DEFAULT 0,
    rejected_chunks INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunk_jobs (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS quiz_results (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    concept_id TEXT NOT NULL,
    correct INTEGER NOT NULL,
    score INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    git_sha TEXT,
    passed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_metrics (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    gate REAL,
    passed INTEGER NOT NULL,
    FOREIGN KEY(run_id) REFERENCES eval_runs(id)
);
CREATE TABLE IF NOT EXISTS evolution_runs (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    proposal_json TEXT NOT NULL,
    shadow_before_json TEXT,
    shadow_after_json TEXT,
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL
);
"""

TUNABLE_DEFAULTS: dict[str, float] = {
    "trust.quote_threshold": 90.0,
    "trust.embed_sim_threshold": 0.35,
    "extract.max_attempts": 2.0,
    "extract.max_tokens": 8192.0,
    "monitor.failure_rate_threshold": 0.5,
    "monitor.min_chunks": 10.0,
    "synth.candidate_k": 8.0,
    "synth.similarity_floor": 0.75,
}


class OpsDB:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        mode = self.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode != "wal":
            raise RuntimeError(f"WAL mode unavailable for {path}; got journal_mode={mode!r}")
        self.conn.executescript(_SCHEMA)
        self._ensure_column("ingest_sources", "rejected_chunks", "INTEGER NOT NULL DEFAULT 0")

    def _ensure_column(self, table: str, column: str, coltype_and_default: str) -> None:
        """Guarded `ALTER TABLE ... ADD COLUMN` for databases created before this column existed."""
        with self._lock, self.conn:
            cols = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")

    def log_model_call(
        self, *, role: str, provider: str, model: str,
        input_tokens: int | None = None, output_tokens: int | None = None,
        latency_ms: float | None = None, cost_usd: float | None = None,
        outcome: str = "ok", error: str | None = None,
    ) -> None:
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO model_calls (ts, role, provider, model, input_tokens,"
                    " output_tokens, latency_ms, cost_usd, outcome, error)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), role, provider, model, input_tokens,
                     output_tokens, latency_ms, cost_usd, outcome, error),
                )

    def recent_calls(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM model_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    def get_tunable(self, key: str) -> float:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM tunables WHERE key = ?", (key,)
            ).fetchone()
        if row is not None:
            return float(row["value"])
        if key not in TUNABLE_DEFAULTS:
            raise KeyError(f"unknown tunable {key!r}")
        return TUNABLE_DEFAULTS[key]

    def set_tunable(self, key: str, value: float, reason: str) -> None:
        if key not in TUNABLE_DEFAULTS:
            raise KeyError(f"unknown tunable {key!r}")
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO tunables (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, float(value)),
                )
                self.conn.execute(
                    "INSERT INTO tunable_audit (ts, key, value, reason) VALUES (?, ?, ?, ?)",
                    (time.time(), key, float(value), reason),
                )

    def tunable_history(self, key: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, key, value, reason FROM tunable_audit"
                " WHERE key = ? ORDER BY id DESC",
                (key,),
            ).fetchall()
        return [dict(r) for r in rows]

    def register_source(self, source_id, ref, role, total_chunks) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO ingest_sources"
                " (source_id, ref, role, total_chunks, ts) VALUES (?, ?, ?, ?, ?)",
                (source_id, ref, role, total_chunks, time.time()),
            )

    def set_source_status(self, source_id, status, error=None, clear_error=False) -> None:
        with self._lock, self.conn:
            if clear_error:
                self.conn.execute(
                    "UPDATE ingest_sources SET status = ?, error = ? WHERE source_id = ?",
                    (status, error, source_id),
                )
            else:
                self.conn.execute(
                    "UPDATE ingest_sources SET status = ?, error = COALESCE(?, error)"
                    " WHERE source_id = ?",
                    (status, error, source_id),
                )

    def source_row(self, source_id) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM ingest_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def all_sources(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM ingest_sources ORDER BY ts").fetchall()
        return [dict(r) for r in rows]

    def register_chunk_jobs(self, source_id, chunk_ids) -> None:
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO chunk_jobs (chunk_id, source_id) VALUES (?, ?)",
                [(cid, source_id) for cid in chunk_ids],
            )

    def mark_chunk(self, chunk_id, status, error=None) -> None:
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT source_id, status FROM chunk_jobs WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            self.conn.execute(
                "UPDATE chunk_jobs SET status = ?, error = ?,"
                " attempts = attempts + 1 WHERE chunk_id = ?",
                (status, error, chunk_id),
            )
            terminal = ("done", "failed", "rejected")
            if row and status in terminal and row["status"] not in terminal:
                column = {"done": "done_chunks", "failed": "failed_chunks",
                          "rejected": "rejected_chunks"}[status]
                self.conn.execute(
                    f"UPDATE ingest_sources SET {column} = {column} + 1 WHERE source_id = ?",
                    (row["source_id"],),
                )

    def pending_chunks(self, source_id) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT chunk_id FROM chunk_jobs WHERE source_id = ? AND status = 'pending'"
                " ORDER BY chunk_id",
                (source_id,),
            ).fetchall()
        return [r["chunk_id"] for r in rows]

    def try_complete_source(self, source_id: str) -> bool:
        """Atomically flip running -> done|failed when all chunks are terminal.

        A source whose chunks ALL failed (infrastructure/model errors) is
        honestly reported as `failed` (never `done`) so the UI doesn't lie
        about a source with zero successful extractions. A source whose
        chunks were all `rejected` by the trust gate (model worked, but
        found nothing trustworthy) still ends `done` with zero claims — the
        pipeline behaved correctly. Returns True exactly once per source,
        and only when the source completed as `done` — synthesizing after a
        fully-failed source is pointless; safe under concurrent workers via
        a single atomic UPDATE.
        """
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE ingest_sources SET"
                " status = CASE WHEN total_chunks > 0 AND failed_chunks = total_chunks"
                "   THEN 'failed' ELSE 'done' END,"
                " error = CASE WHEN total_chunks > 0 AND failed_chunks = total_chunks"
                "   THEN 'all ' || total_chunks || ' chunks failed' ELSE error END"
                " WHERE source_id = ? AND status = 'running'"
                " AND done_chunks + failed_chunks + rejected_chunks >= total_chunks",
                (source_id,),
            )
            if cursor.rowcount != 1:
                return False
            row = self.conn.execute(
                "SELECT status FROM ingest_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            return row["status"] == "done"

    def failure_groups(self, source_id: str) -> list[dict]:
        """Group failed chunk_jobs by error message for a plain-language failures view."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT chunk_id, error FROM chunk_jobs"
                " WHERE source_id = ? AND status = 'failed' ORDER BY chunk_id",
                (source_id,),
            ).fetchall()
        groups: dict[str, list[str]] = {}
        for row in rows:
            groups.setdefault(row["error"] or "unknown error", []).append(row["chunk_id"])
        return [
            {"error": error, "count": len(ids), "sample_chunk_ids": ids[:3]}
            for error, ids in sorted(groups.items(), key=lambda kv: -len(kv[1]))
        ]

    def reset_failed_chunks(self, source_id: str) -> list[str]:
        """Reset failed/skipped_paused chunk_jobs to pending so they can be retried.

        Decrements failed_chunks accordingly (skipped_paused was never counted).
        Returns the chunk ids that were reset.
        """
        with self._lock, self.conn:
            rows = self.conn.execute(
                "SELECT chunk_id, status FROM chunk_jobs"
                " WHERE source_id = ? AND status IN ('failed', 'skipped_paused')",
                (source_id,),
            ).fetchall()
            ids = [row["chunk_id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            self.conn.execute(
                f"UPDATE chunk_jobs SET status = 'pending', error = NULL, attempts = 0"
                f" WHERE chunk_id IN ({placeholders})",
                ids,
            )
            failed_count = sum(1 for row in rows if row["status"] == "failed")
            if failed_count:
                self.conn.execute(
                    "UPDATE ingest_sources SET failed_chunks = failed_chunks - ?"
                    " WHERE source_id = ?",
                    (failed_count, source_id),
                )
            return ids

    def failure_stats(self, source_id) -> dict:
        """Counts feeding the failure-rate monitor.

        `failed` = infrastructure/model errors, `rejected` = the trust gate
        correctly declined every claim a chunk produced. The monitor trips
        on `problems` (both combined) but the UI reports them separately —
        a high rejection rate is not the same signal as a high error rate.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT count(*) AS total,"
                " sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,"
                " sum(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected"
                " FROM chunk_jobs WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        failed = row["failed"] or 0
        rejected = row["rejected"] or 0
        return {"total": row["total"], "failed": failed, "rejected": rejected,
                "problems": failed + rejected}

    def record_quiz_result(self, concept_id: str, correct: bool, score: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO quiz_results (ts, concept_id, correct, score) VALUES (?, ?, ?, ?)",
                (time.time(), concept_id, 1 if correct else 0, int(score)),
            )

    def quiz_stats(self, concept_id: str | None = None):
        query = "SELECT * FROM quiz_results"
        params: tuple[str, ...] = ()
        if concept_id is not None:
            query += " WHERE concept_id = ?"
            params = (concept_id,)
        query += " ORDER BY concept_id, id"
        with self._lock:
            rows = [dict(row) for row in self.conn.execute(query, params).fetchall()]

        if concept_id is not None:
            return _quiz_aggregate(concept_id, rows)

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["concept_id"], []).append(row)
        return [_quiz_aggregate(cid, grouped[cid]) for cid in sorted(grouped)]

    def create_eval_run(self, kind: str, git_sha: str | None, passed: bool) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO eval_runs (ts, kind, git_sha, passed) VALUES (?, ?, ?, ?)",
                (time.time(), kind, git_sha, 1 if passed else 0),
            )
            return int(cur.lastrowid)

    def add_eval_metric(
        self, run_id: int, metric: str, value: float, gate: float | None, passed: bool
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO eval_metrics (run_id, metric, value, gate, passed)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, metric, float(value), gate, 1 if passed else 0),
            )

    def latest_eval_run(self) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM eval_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def eval_metrics_for_run(self, run_id: int) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT metric, value, gate, passed FROM eval_metrics WHERE run_id = ?"
                " ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def eval_history(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def create_evolution_run(
        self,
        *,
        proposal_json: str,
        shadow_before_json: str,
        shadow_after_json: str,
        accepted: bool,
        reason: str,
    ) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO evolution_runs"
                " (ts, proposal_json, shadow_before_json, shadow_after_json, accepted, reason)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    proposal_json,
                    shadow_before_json,
                    shadow_after_json,
                    1 if accepted else 0,
                    reason,
                ),
            )
            return int(cur.lastrowid)

    def evolution_history(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM evolution_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def _quiz_aggregate(concept_id: str, rows: list[dict]) -> dict:
    attempts = len(rows)
    correct = sum(1 for row in rows if bool(row["correct"]))
    last = rows[-1] if rows else None
    return {
        "concept_id": concept_id,
        "attempts": attempts,
        "correct": correct,
        "incorrect": attempts - correct,
        "avg_score": (sum(row["score"] for row in rows) / attempts) if attempts else 0.0,
        "last_score": last["score"] if last else None,
        "last_correct": bool(last["correct"]) if last else None,
        "last_ts": last["ts"] if last else None,
    }
