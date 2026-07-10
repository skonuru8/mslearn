from mslearn.opsdb import OpsDB


def test_synth_concurrency_default(tmp_path):
    db = OpsDB(tmp_path / "x.db")
    assert db.get_tunable("synth.concurrency") == 8.0
