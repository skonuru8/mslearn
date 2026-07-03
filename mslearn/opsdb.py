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
"""

TUNABLE_DEFAULTS: dict[str, float] = {
    "trust.quote_threshold": 90.0,
    "trust.embed_sim_threshold": 0.35,
    "extract.max_attempts": 2.0,
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

    def set_source_status(self, source_id, status, error=None) -> None:
        with self._lock, self.conn:
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
            if row and status in ("done", "failed") and row["status"] not in ("done", "failed"):
                column = "done_chunks" if status == "done" else "failed_chunks"
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
        """Atomically flip running->done when all chunks are accounted for.

        Returns True exactly once per source; safe under concurrent workers.
        """
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE ingest_sources SET status = 'done'"
                " WHERE source_id = ? AND status = 'running'"
                " AND done_chunks + failed_chunks >= total_chunks",
                (source_id,),
            )
            return cursor.rowcount == 1

    def failure_stats(self, source_id) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT count(*) AS total,"
                " sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed"
                " FROM chunk_jobs WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return {"total": row["total"], "failed": row["failed"] or 0}
