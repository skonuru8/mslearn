def test_guides_queued_single_flight(tmp_path):
    from mslearn.opsdb import OpsDB
    db = OpsDB(tmp_path / "x.db")
    assert db.try_mark_guides_queued("p") is True
    assert db.try_mark_guides_queued("p") is False
    db.clear_guides_queued("p")
    assert db.try_mark_guides_queued("p") is True
