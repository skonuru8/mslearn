from mslearn.opsdb import TUNABLE_DEFAULTS, OpsDB


def test_extract_max_claims_default():
    assert TUNABLE_DEFAULTS["extract.max_claims"] == 15.0


def test_log_and_read_model_call(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.log_model_call(
        role="extraction", provider="ollama", model="m", input_tokens=10,
        output_tokens=20, latency_ms=123.4, outcome="ok",
    )
    db.log_model_call(role="synthesis", provider="openrouter", model="m2",
                      outcome="error", error="boom")
    calls = db.recent_calls()
    assert len(calls) == 2
    assert calls[0]["role"] == "synthesis" and calls[0]["error"] == "boom"  # newest first
    assert calls[1]["output_tokens"] == 20


def test_settings_kv_roundtrip_and_upsert(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert db.get_setting("active_profile") is None
    assert db.get_setting("active_profile", "openrouter") == "openrouter"
    db.set_setting("active_profile", "offline")
    db.set_setting("active_profile", "claude-code")
    assert db.get_setting("active_profile") == "claude-code"


def test_creates_parent_dirs(tmp_path):
    OpsDB(tmp_path / "nested" / "dir" / "ops.db")  # must not raise


def test_wal_mode_is_active(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_study_progress_roundtrip(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.set_section_reviewed("default", "con1", "s1", True)
    assert db.section_progress("default", "con1") == {"s1": True}
    db.set_section_reviewed("default", "con1", "s1", False)
    assert db.section_progress("default", "con1") == {"s1": False}


def test_guide_max_tokens_default():
    assert TUNABLE_DEFAULTS["guide.max_tokens"] == 8192.0


def test_concurrent_writes_from_threads(tmp_path):
    import threading

    db = OpsDB(tmp_path / "ops.db")

    def write_batch():
        for _ in range(20):
            db.log_model_call(role="r", provider="p", model="m", outcome="ok")

    threads = [threading.Thread(target=write_batch) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(db.recent_calls(limit=500)) == 200
