import pytest

from mslearn.memory.base import LearnerMemory, MemoryItem
from mslearn.memory.sqlite_memory import SqliteMemory
from mslearn.opsdb import OpsDB
from tests.fakes import InMemoryLearnerMemory, ScriptedRouter


def test_in_memory_learner_memory_protocol():
    mem: LearnerMemory = InMemoryLearnerMemory()
    mid = mem.add("prefers short examples", "preference")
    assert isinstance(mid, str) and mid

    items = mem.search("short", k=5)
    assert len(items) == 1
    assert items[0].text == "prefers short examples"
    assert items[0].category == "preference"
    assert isinstance(items[0], MemoryItem)

    mem.add("struggled with recursion", "struggle")
    assert len(mem.all()) == 2
    assert len(mem.search("recursion")) == 1
    assert len(mem.search("missing")) == 0

    mem.delete(mid)
    assert len(mem.all()) == 1
    assert mem.search("short") == []


def test_build_default_context_memory_none_when_sqlite_memory_missing(monkeypatch, tmp_path):
    # build_default_context keeps its try/except: even if constructing the
    # in-house memory itself somehow fails, memory=None must not take down
    # worker startup.
    from mslearn.settings import Settings
    from mslearn.worker.context import build_default_context

    monkeypatch.setattr(
        "mslearn.settings.get_settings",
        lambda: Settings(
            data_dir=tmp_path / "data",
            profiles_path="profiles.yaml",
            neo4j_uri="bolt://localhost:7687",
            neo4j_user="neo4j",
            neo4j_password="learnsys",
        ),
    )

    def _raise_import(*_args, **_kwargs):
        raise RuntimeError("memory backend construction failed")

    monkeypatch.setattr("mslearn.memory.sqlite_memory.SqliteMemory", _raise_import)

    ctx = build_default_context()
    assert ctx.memory is None


def test_sqlite_memory_add_search_ranks_by_cosine_similarity(tmp_path):
    router = ScriptedRouter(
        embeddings=[
            [1.0, 0.0],  # add: "prefers short examples"
            [0.0, 1.0],  # add: "struggled with recursion"
            [1.0, 0.0],  # search query: close to the first item
        ]
    )
    db = OpsDB(tmp_path / "ops.db")
    mem = SqliteMemory(db, router)

    mid = mem.add("prefers short examples", "preference")
    mem.add("struggled with recursion", "struggle")

    hits = mem.search("short examples please", k=5)

    assert len(hits) == 2
    assert hits[0].memory_id == mid
    assert hits[0].text == "prefers short examples"
    assert hits[0].category == "preference"
    assert isinstance(hits[0], MemoryItem)


def test_sqlite_memory_search_respects_k(tmp_path):
    router = ScriptedRouter(embeddings=[[1.0, 0.0]] * 4)
    db = OpsDB(tmp_path / "ops.db")
    mem = SqliteMemory(db, router)
    mem.add("one", "interaction")
    mem.add("two", "interaction")
    mem.add("three", "interaction")

    hits = mem.search("query", k=2)

    assert len(hits) == 2


def test_sqlite_memory_is_project_scoped(tmp_path):
    router = ScriptedRouter(embeddings=[[1.0, 0.0]] * 4)
    db = OpsDB(tmp_path / "ops.db")
    mem = SqliteMemory(db, router)
    mem.add("default project note", "interaction", project_id="default")
    mem.add("other project note", "interaction", project_id="other")

    default_items = mem.all(project_id="default")
    other_items = mem.all(project_id="other")

    assert [item.text for item in default_items] == ["default project note"]
    assert [item.text for item in other_items] == ["other project note"]
    default_hits = mem.search("note", k=5, project_id="default")
    assert [item.text for item in default_hits] == ["default project note"]


def test_sqlite_memory_all_and_delete(tmp_path):
    router = ScriptedRouter(embeddings=[[1.0, 0.0], [0.0, 1.0]])
    db = OpsDB(tmp_path / "ops.db")
    mem = SqliteMemory(db, router)
    first = mem.add("first note", "interaction")
    mem.add("second note", "interaction")

    assert len(mem.all()) == 2
    mem.delete(first)
    remaining = mem.all()
    assert len(remaining) == 1
    assert remaining[0].text == "second note"


def test_sqlite_memory_disables_after_embedding_failure(tmp_path):
    # An embedding-backend outage (e.g. Ollama unreachable) must not surface
    # as a broken teach/quiz/chat endpoint, and must not keep re-attempting
    # the same failing embed call on every request.
    class ExplodingRouter:
        def embed(self, texts):
            raise RuntimeError("embedder unreachable")

    db = OpsDB(tmp_path / "ops.db")
    mem = SqliteMemory(db, ExplodingRouter())

    with pytest.raises(RuntimeError):
        mem.add("some text", "interaction")
    assert mem._disabled is True

    # Short-circuits: no further attempt to embed, no exception.
    assert mem.add("more text", "interaction") == ""
    assert mem.search("anything") == []


def test_sqlite_memory_search_disables_after_embedding_failure(tmp_path):
    class ExplodingAfterFirstAdd:
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 1:
                return [[1.0, 0.0] for _ in texts]
            raise RuntimeError("embedder unreachable")

    db = OpsDB(tmp_path / "ops.db")
    router = ExplodingAfterFirstAdd()
    mem = SqliteMemory(db, router)
    mem.add("some text", "interaction")

    with pytest.raises(RuntimeError):
        mem.search("query")
    assert mem._disabled is True
    assert mem.search("query") == []


def test_sqlite_memory_survives_opsdb_restart(tmp_path):
    # learner_memory rows live in OpsDB (sqlite), not a process-local dict —
    # a fresh OpsDB handle on the same file must still see them.
    router = ScriptedRouter(embeddings=[[1.0, 0.0]])
    db_path = tmp_path / "ops.db"
    db = OpsDB(db_path)
    mem = SqliteMemory(db, router)
    mem.add("persisted note", "interaction")

    restarted_db = OpsDB(db_path)
    restarted_mem = SqliteMemory(restarted_db, ScriptedRouter(embeddings=[[1.0, 0.0]]))
    items = restarted_mem.all()
    assert len(items) == 1
    assert items[0].text == "persisted note"
