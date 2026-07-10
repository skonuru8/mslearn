from mslearn.opsdb import OpsDB


def test_synth_concurrency_default(tmp_path):
    db = OpsDB(tmp_path / "x.db")
    assert db.get_tunable("synth.concurrency") == 24.0


def test_synth_concurrency_and_match_batch(tmp_path):
    from mslearn.opsdb import OpsDB
    db = OpsDB(tmp_path / "x.db")
    assert db.get_tunable("synth.concurrency") == 24.0
    assert db.get_tunable("synth.match_batch") == 8.0
