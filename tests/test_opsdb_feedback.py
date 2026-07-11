from mslearn.opsdb import OpsDB


def test_feedback_insert_and_aggregate(tmp_path):
    db = OpsDB(tmp_path / "o.db")
    db.add_note_feedback("default", "k1", helpful=True, tags=["too_shallow"], comment="x", guide_hash="h1")
    db.add_note_feedback("default", "k2", helpful=False, tags=["wrong", "repetitive"], comment="", guide_hash="h2")
    agg = db.feedback_aggregate("default")
    assert agg["total_rated"] == 2
    assert agg["helpful"] == 1
    assert agg["too_shallow"] == 1
    assert agg["wrong"] == 1
    neg = db.recent_negative_feedback("default")
    assert any(r["concept_id"] == "k2" for r in neg)
    assert db.feedback_for_concept("k1", "default")["tags"] == ["too_shallow"]
