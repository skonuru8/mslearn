from mslearn.opsdb import OpsDB


def db(tmp_path):
    return OpsDB(tmp_path / "ops.db")


def test_source_and_chunk_lifecycle(tmp_path):
    d = db(tmp_path)
    d.register_source("s1", ref="/x.pdf", role="spine", total_chunks=3)
    d.register_chunk_jobs("s1", ["s1:0", "s1:1", "s1:2"])
    assert d.pending_chunks("s1") == ["s1:0", "s1:1", "s1:2"]
    d.mark_chunk("s1:0", "done")
    d.mark_chunk("s1:1", "failed", error="boom")
    assert d.pending_chunks("s1") == ["s1:2"]
    row = d.source_row("s1")
    assert row["done_chunks"] == 1 and row["failed_chunks"] == 1
    assert d.failure_stats("s1") == {"total": 3, "failed": 1}


def test_source_status_transitions(tmp_path):
    d = db(tmp_path)
    d.register_source("s1", ref="r", role="supplement", total_chunks=1)
    assert d.source_row("s1")["status"] == "registered"
    d.set_source_status("s1", "paused")
    assert d.source_row("s1")["status"] == "paused"
    d.set_source_status("s1", "failed", error="unparseable")
    assert d.source_row("s1")["error"] == "unparseable"


def test_register_idempotent(tmp_path):
    d = db(tmp_path)
    d.register_source("s1", ref="r", role="spine", total_chunks=2)
    d.register_source("s1", ref="r", role="spine", total_chunks=2)  # no crash, no dup
    d.register_chunk_jobs("s1", ["s1:0"])
    d.register_chunk_jobs("s1", ["s1:0"])
    assert d.pending_chunks("s1") == ["s1:0"]
    assert len(d.all_sources()) == 1
