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
"""


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
