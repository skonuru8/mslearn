from mslearn.opsdb import OpsDB


def test_pending_evolution_run_lifecycle(tmp_path):
    db = OpsDB(tmp_path / "o.db")
    run_id = db.create_evolution_run(
        proposal_json="{}",
        shadow_before_json="{}",
        shadow_after_json="{}",
        accepted=False,
        reason="prompt rewrite awaiting approval",
        status="pending",
    )

    pending = db.pending_evolution_runs()
    assert any(r["id"] == run_id for r in pending)

    db.set_evolution_run_status(run_id, "applied")

    pending_after = db.pending_evolution_runs()
    assert not any(r["id"] == run_id for r in pending_after)

    history = db.evolution_history()
    row = next(r for r in history if r["id"] == run_id)
    assert row["status"] == "applied"


def test_create_evolution_run_defaults_status_applied(tmp_path):
    db = OpsDB(tmp_path / "o.db")
    run_id = db.create_evolution_run(
        proposal_json="{}",
        shadow_before_json="{}",
        shadow_after_json="{}",
        accepted=True,
        reason="tunable auto-applied",
    )
    history = db.evolution_history()
    row = next(r for r in history if r["id"] == run_id)
    assert row["status"] == "applied"


def test_existing_rows_migrate_to_applied_status(tmp_path):
    # An evolution_runs row written before the status column existed should
    # come back as 'applied' via the _ensure_column default, not NULL/crash.
    path = tmp_path / "o.db"
    db = OpsDB(path)
    db.conn.execute(
        "INSERT INTO evolution_runs"
        " (ts, proposal_json, shadow_before_json, shadow_after_json, accepted, reason)"
        " VALUES (0, '{}', '{}', '{}', 1, 'legacy row')"
    )
    db.conn.commit()
    history = db.evolution_history()
    row = next(r for r in history if r["reason"] == "legacy row")
    assert row["status"] == "applied"
