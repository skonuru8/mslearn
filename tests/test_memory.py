import sys

from mslearn.memory.base import LearnerMemory, MemoryItem
from tests.fakes import InMemoryLearnerMemory


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


def test_mem0_import_is_lazy():
    import mslearn.memory.mem0_impl  # noqa: F401

    assert "mem0" not in sys.modules


def test_build_default_context_memory_none_when_mem0_missing(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mem0" or name.startswith("mem0."):
            raise ImportError("mem0 not installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

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

    ctx = build_default_context()
    assert ctx.memory is None
