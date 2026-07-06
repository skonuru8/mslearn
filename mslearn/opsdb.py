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
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session
    ON chat_turns (project_id, session_id, id);
CREATE TABLE IF NOT EXISTS learner_memory (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    category TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learner_memory_project
    ON learner_memory (project_id, created_ts);
"""

DEFAULT_PROJECT_ID = "default"

# TTLs for the synthesis-enqueue dedup marker (try_mark_synthesis_queued):
# a queued-but-not-yet-started trigger is considered stale after 15 minutes
# (a worker should have picked it up long before then); a running marker is
# considered stale after 30 minutes so a crashed worker that never cleared
# `synthesis:running_since` can't wedge synthesis forever.
SYNTHESIS_QUEUED_TTL_S = 15 * 60
SYNTHESIS_RUNNING_TTL_S = 30 * 60


def project_setting_key(project_id: str, key: str) -> str:
    return f"project:{project_id}:{key}"

TUNABLE_DEFAULTS: dict[str, float] = {
    "trust.quote_threshold": 90.0,
    "trust.embed_sim_threshold": 0.35,
    "extract.max_attempts": 2.0,
    # 8192 was sized for hidden-reasoning overhead (Plan 09). With reasoning
    # disabled on every openrouter role (profiles.yaml), the budget only has
    # to cover the answer itself: measured extraction outputs over 656 live
    # calls were p99 = 914 tokens, max = 1280 — 4096 is >3x the observed max.
    # This is only the DEFAULT: a DB with a stored tunables row (set via the
    # admin API or self-evolution) keeps its stored value.
    "extract.max_tokens": 4096.0,
    "monitor.failure_rate_threshold": 0.5,
    "monitor.min_chunks": 10.0,
    "synth.candidate_k": 8.0,
    "synth.similarity_floor": 0.75,
    # Reasoning models (deepseek-v4-flash) can burn the whole completion
    # budget on hidden reasoning tokens before writing any answer text —
    # 2048 (base.py ModelRequest default) is not enough headroom. Mirrors
    # extract.max_tokens (Plan 09); one tunable per callsite rather than a
    # single shared value so each can be tuned independently later.
    "synth.max_tokens": 8192.0,
    "chat.max_tokens": 8192.0,
    "quiz.max_tokens": 8192.0,
    "teach.max_tokens": 8192.0,
    "evolve.max_tokens": 8192.0,
    # Image transcription output can be long (a dense screenshot / slide with
    # lots of text). The vision model is not a reasoning model, so this is a
    # pure output budget.
    "image.max_tokens": 4096.0,
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
        self._ensure_column("ingest_sources", "project_id", "TEXT NOT NULL DEFAULT 'default'")
        self._ensure_column("chunk_jobs", "project_id", "TEXT NOT NULL DEFAULT 'default'")
        self._ensure_column("quiz_results", "project_id", "TEXT NOT NULL DEFAULT 'default'")
        self._bootstrap_projects()
        self._migrate_legacy_project_settings()

    def _bootstrap_projects(self) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO projects (project_id, name, created_ts)"
                " VALUES (?, ?, ?)",
                (DEFAULT_PROJECT_ID, "Default project", time.time()),
            )

    def _migrate_legacy_project_settings(self) -> None:
        """Copy pre-project global corpus keys into the default project scope once."""
        legacy = {
            "corpus.domain_profile": "technical",
            "synthesis:last_run": None,
        }
        with self._lock, self.conn:
            for key, default in legacy.items():
                scoped = project_setting_key(DEFAULT_PROJECT_ID, key)
                if self.conn.execute(
                    "SELECT 1 FROM settings WHERE key = ?", (scoped,)
                ).fetchone():
                    continue
                row = self.conn.execute(
                    "SELECT value FROM settings WHERE key = ?", (key,)
                ).fetchone()
                if row is not None:
                    self.conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO NOTHING",
                        (scoped, row["value"]),
                    )
                elif default is not None:
                    self.conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO NOTHING",
                        (scoped, default),
                    )

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

    def spend_totals(self) -> dict:
        """Cheap aggregate for a polling status chip — avoids shipping every
        model_calls row (recent_calls) just to render two numbers."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS total_calls,"
                " COALESCE(SUM(cost_usd), 0) AS total_cost_usd FROM model_calls"
            ).fetchone()
        return {"total_calls": row["total_calls"], "total_cost_usd": row["total_cost_usd"]}

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

    def delete_setting(self, key: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def get_project_setting(
        self, project_id: str, key: str, default: str | None = None
    ) -> str | None:
        return self.get_setting(project_setting_key(project_id, key), default)

    def set_project_setting(self, project_id: str, key: str, value: str) -> None:
        self.set_setting(project_setting_key(project_id, key), value)

    def delete_project_setting(self, project_id: str, key: str) -> None:
        self.delete_setting(project_setting_key(project_id, key))

    def try_mark_synthesis_queued(self, project_id: str, *, now: float | None = None) -> bool:
        """Atomically claim the right to enqueue a synthesis run for `project_id`.

        Collapses N near-simultaneous triggers (Build button double-clicks,
        delete_source's rebuild, try_complete_source's auto-fire, claim
        flagging) into at most one queued follow-up: returns False when a
        fresh queued marker (< `SYNTHESIS_QUEUED_TTL_S`) or a fresh running
        marker (< `SYNTHESIS_RUNNING_TTL_S`, in case a worker crashed mid-run
        without clearing it) already exists, True (and sets the queued
        marker) otherwise. Single atomic transaction under the same lock
        every other OpsDB write uses — safe under concurrent callers from
        both the API process and worker processes sharing this sqlite file.
        Redis stays broker-only; this marker lives here, not in the broker.
        """
        now = time.time() if now is None else now
        running_key = project_setting_key(project_id, "synthesis:running_since")
        queued_key = project_setting_key(project_id, "synthesis:queued")
        with self._lock, self.conn:
            running_row = self.conn.execute(
                "SELECT value FROM settings WHERE key = ?", (running_key,)
            ).fetchone()
            if running_row and running_row["value"]:
                try:
                    running_ts = float(running_row["value"])
                except ValueError:
                    running_ts = 0.0
                if now - running_ts < SYNTHESIS_RUNNING_TTL_S:
                    return False
            queued_row = self.conn.execute(
                "SELECT value FROM settings WHERE key = ?", (queued_key,)
            ).fetchone()
            if queued_row and queued_row["value"]:
                try:
                    queued_ts = float(queued_row["value"])
                except ValueError:
                    queued_ts = 0.0
                if now - queued_ts < SYNTHESIS_QUEUED_TTL_S:
                    return False
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (queued_key, str(now)),
            )
            return True

    def clear_synthesis_queued(self, project_id: str) -> None:
        """Called when a synthesis run actually starts (synthesize_task), so
        the next trigger after this run finishes is free to queue again."""
        self.delete_project_setting(project_id, "synthesis:queued")

    def create_project(self, project_id: str, name: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO projects (project_id, name, created_ts) VALUES (?, ?, ?)",
                (project_id, name, time.time()),
            )

    def list_projects(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT project_id, name, created_ts FROM projects ORDER BY created_ts"
            ).fetchall()
        return [dict(r) for r in rows]

    def project_exists(self, project_id: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        return row is not None

    def delete_project(self, project_id: str) -> None:
        if project_id == DEFAULT_PROJECT_ID:
            raise ValueError("cannot delete the default project")
        with self._lock, self.conn:
            source_ids = [
                r["source_id"]
                for r in self.conn.execute(
                    "SELECT source_id FROM ingest_sources WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
            ]
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                self.conn.execute(
                    f"DELETE FROM chunk_jobs WHERE source_id IN ({placeholders})",
                    source_ids,
                )
            self.conn.execute(
                "DELETE FROM ingest_sources WHERE project_id = ?", (project_id,)
            )
            self.conn.execute(
                "DELETE FROM quiz_results WHERE project_id = ?", (project_id,)
            )
            self.conn.execute(
                "DELETE FROM settings WHERE key LIKE ?", (f"project:{project_id}:%",)
            )
            self.conn.execute(
                "DELETE FROM chat_turns WHERE project_id = ?", (project_id,)
            )
            self.conn.execute(
                "DELETE FROM projects WHERE project_id = ?", (project_id,)
            )

    def delete_source(self, source_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "DELETE FROM chunk_jobs WHERE source_id = ? AND project_id = ?",
                (source_id, project_id),
            )
            self.conn.execute(
                "DELETE FROM ingest_sources WHERE source_id = ? AND project_id = ?",
                (source_id, project_id),
            )

    def project_id_for_chunk(self, chunk_id: str) -> str:
        with self._lock:
            row = self.conn.execute(
                "SELECT s.project_id FROM chunk_jobs c"
                " JOIN ingest_sources s ON c.source_id = s.source_id"
                " WHERE c.chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        return row["project_id"] if row else DEFAULT_PROJECT_ID

    def project_id_for_source(self, source_id: str) -> str:
        with self._lock:
            row = self.conn.execute(
                "SELECT project_id FROM ingest_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return row["project_id"] if row else DEFAULT_PROJECT_ID

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

    def register_source(self, source_id, ref, role, total_chunks, project_id=DEFAULT_PROJECT_ID) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO ingest_sources"
                " (source_id, ref, role, total_chunks, ts, project_id) VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, ref, role, total_chunks, time.time(), project_id),
            )

    def set_source_total_chunks(self, source_id, total_chunks: int) -> None:
        """Backfill total_chunks once chunking finishes (registered at 0 while "chunking")."""
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE ingest_sources SET total_chunks = ? WHERE source_id = ?",
                (total_chunks, source_id),
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

    def source_row(self, source_id, project_id: str | None = None) -> dict | None:
        with self._lock:
            if project_id is not None:
                row = self.conn.execute(
                    "SELECT * FROM ingest_sources WHERE source_id = ? AND project_id = ?",
                    (source_id, project_id),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM ingest_sources WHERE source_id = ?", (source_id,)
                ).fetchone()
        return dict(row) if row else None

    def all_sources(self, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM ingest_sources WHERE project_id = ? ORDER BY ts",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def register_chunk_jobs(self, source_id, chunk_ids, project_id=DEFAULT_PROJECT_ID) -> None:
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO chunk_jobs (chunk_id, source_id, project_id)"
                " VALUES (?, ?, ?)",
                [(cid, source_id, project_id) for cid in chunk_ids],
            )

    def mark_chunk(self, chunk_id, status, error=None) -> None:
        """Transition a chunk job's status, atomically guarding against double-counting.

        A redelivered Celery task (retried across worker processes, not just
        within one) could otherwise race a read-then-write terminal-state
        check and double-increment done_chunks/failed_chunks/rejected_chunks.
        The status/error/attempts write itself is now gated by the same
        single atomic UPDATE (WHERE status NOT IN the terminal set) so an
        already-terminal chunk can't be re-processed at all, not just
        re-counted.
        """
        terminal = ("done", "failed", "rejected")
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT source_id FROM chunk_jobs WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            if row is None:
                return
            cursor = self.conn.execute(
                "UPDATE chunk_jobs SET status = ?, error = ?, attempts = attempts + 1"
                " WHERE chunk_id = ? AND status NOT IN ('done', 'failed', 'rejected')",
                (status, error, chunk_id),
            )
            if cursor.rowcount == 1 and status in terminal:
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

    def record_quiz_result(
        self, concept_id: str, correct: bool, score: int, project_id: str = DEFAULT_PROJECT_ID
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO quiz_results (ts, concept_id, correct, score, project_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (time.time(), concept_id, 1 if correct else 0, int(score), project_id),
            )

    def quiz_stats(self, concept_id: str | None = None, project_id: str = DEFAULT_PROJECT_ID):
        query = "SELECT * FROM quiz_results WHERE project_id = ?"
        params: tuple[str, ...] = (project_id,)
        if concept_id is not None:
            query += " AND concept_id = ?"
            params = (project_id, concept_id)
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

    def set_evolution_run_accepted(self, run_id: int, accepted: bool) -> None:
        """Flip an evolution_runs row's accepted flag after the accept decision is made.

        create_evolution_run always writes accepted=False up front (the row is
        created before the decision exists); this is the only way that flag
        is ever set True, so history/API views reflect what was actually applied.
        """
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE evolution_runs SET accepted = ? WHERE id = ?",
                (1 if accepted else 0, run_id),
            )

    def evolution_history(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM evolution_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def append_chat_turn(
        self, project_id: str, session_id: str, question: str, answer: str
    ) -> None:
        """Persist a chat turn so history survives an API restart."""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO chat_turns (ts, project_id, session_id, question, answer)"
                " VALUES (?, ?, ?, ?, ?)",
                (time.time(), project_id, session_id, question, answer),
            )

    def chat_turns(self, project_id: str, session_id: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT question, answer, ts FROM chat_turns"
                " WHERE project_id = ? AND session_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (project_id, session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_memory_item(
        self,
        *,
        memory_id: str,
        project_id: str,
        category: str,
        text: str,
        embedding_json: str,
        created_ts: float,
    ) -> None:
        """Persist one learner-memory row (SqliteMemory backend, Plan 16)."""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO learner_memory"
                " (memory_id, project_id, category, text, embedding, created_ts)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, project_id, category, text, embedding_json, created_ts),
            )

    def memory_items(self, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT memory_id, category, text, embedding, created_ts FROM learner_memory"
                " WHERE project_id = ? ORDER BY created_ts",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory_item(self, memory_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM learner_memory WHERE memory_id = ?", (memory_id,))


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
