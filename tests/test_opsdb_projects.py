from __future__ import annotations

import sqlite3

import pytest

from mslearn.opsdb import DEFAULT_PROJECT_ID, OpsDB


def test_projects_bootstrap_and_migration_are_idempotent(tmp_path):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        ("corpus.domain_profile", "interpretive"),
    )
    conn.commit()
    conn.close()

    db = OpsDB(path)
    db.conn.close()
    db = OpsDB(path)

    projects = db.list_projects()
    assert [row["project_id"] for row in projects] == [DEFAULT_PROJECT_ID]
    assert db.get_project_setting(DEFAULT_PROJECT_ID, "corpus.domain_profile") == "interpretive"


def test_project_crud_settings_and_delete_scope(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_project("alpha", "Alpha")
    db.set_project_setting("alpha", "corpus.domain_profile", "interpretive")
    db.register_source("src-alpha", "alpha.pdf", "spine", 2, project_id="alpha")
    db.register_chunk_jobs("src-alpha", ["src-alpha:0", "src-alpha:1"], project_id="alpha")
    db.record_quiz_result("k-alpha", True, 100, project_id="alpha")

    assert db.project_exists("alpha")
    assert db.all_sources("alpha")[0]["source_id"] == "src-alpha"
    assert db.project_id_for_chunk("src-alpha:0") == "alpha"
    assert db.project_id_for_source("src-alpha") == "alpha"
    assert db.quiz_stats(project_id="alpha")[0]["concept_id"] == "k-alpha"

    db.delete_project("alpha")

    assert not db.project_exists("alpha")
    assert db.all_sources("alpha") == []
    assert db.quiz_stats(project_id="alpha") == []
    assert db.get_project_setting("alpha", "corpus.domain_profile") is None


def test_default_project_cannot_be_deleted(tmp_path):
    db = OpsDB(tmp_path / "ops.db")

    with pytest.raises(ValueError, match="default project"):
        db.delete_project(DEFAULT_PROJECT_ID)


def test_existing_tables_gain_project_columns_once(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE ingest_sources (
            source_id TEXT PRIMARY KEY,
            ref TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            total_chunks INTEGER NOT NULL,
            done_chunks INTEGER NOT NULL DEFAULT 0,
            failed_chunks INTEGER NOT NULL DEFAULT 0,
            rejected_chunks INTEGER NOT NULL DEFAULT 0,
            ts REAL NOT NULL
        );
        CREATE TABLE chunk_jobs (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT
        );
        CREATE TABLE quiz_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            concept_id TEXT NOT NULL,
            correct INTEGER NOT NULL,
            score INTEGER NOT NULL
        );
        """
    )
    conn.close()

    db = OpsDB(path)
    db.conn.close()
    db = OpsDB(path)
    db.conn.close()

    conn = sqlite3.connect(path)
    columns = {
        table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for table in ("ingest_sources", "chunk_jobs", "quiz_results")
    }
    assert all("project_id" in names for names in columns.values())
