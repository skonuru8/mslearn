import sys
from pathlib import Path

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
        raise ImportError("mem0 not installed")

    monkeypatch.setattr("mslearn.memory.mem0_impl.Mem0Memory", _raise_import)

    ctx = build_default_context()
    assert ctx.memory is None


def test_mem0_config_points_llm_at_openrouter_not_openai(tmp_path):
    # mem0's OpenAILLM only honors `openrouter_base_url` when the
    # OPENROUTER_API_KEY *environment variable* is set (it isn't — we pass
    # the key through config, not the env). Without this, the "openai"
    # provider silently falls back to https://api.openai.com/v1 and every
    # learner-memory call fails auth using an OpenRouter key against
    # OpenAI's real API. `openai_base_url` is the field that actually
    # takes effect for that fallback path.
    from mslearn.memory.mem0_impl import Mem0Memory
    from mslearn.opsdb import OpsDB
    from mslearn.settings import Settings

    settings = Settings(
        profiles_path=Path("profiles.yaml"),
        openrouter_api_key="sk-or-test",
    )
    db = OpsDB(tmp_path / "ops.db")
    config = Mem0Memory(settings, db)._build_config()

    assert config["llm"]["provider"] == "openai"
    assert config["llm"]["config"]["openai_base_url"] == "https://openrouter.ai/api/v1"
    assert config["llm"]["config"]["api_key"] == "sk-or-test"
    assert "openrouter_base_url" not in config["llm"]["config"]


def test_mem0_embedder_model_comes_from_active_profile(tmp_path):
    # "model IDs live in config, never code": the embedder id must resolve from
    # the active profile's embedding role, not a hardcode.
    from mslearn.memory.mem0_impl import Mem0Memory
    from mslearn.opsdb import OpsDB
    from mslearn.profiles import get_active_profile_name, load_profiles
    from mslearn.settings import Settings

    settings = Settings(profiles_path=Path("profiles.yaml"))
    db = OpsDB(tmp_path / "ops.db")
    profiles = load_profiles(settings.profiles_path)
    active = get_active_profile_name(db, profiles)
    expected = profiles.profiles[active].roles["embedding"].model

    config = Mem0Memory(settings, db)._build_config()
    assert config["embedder"]["config"]["model"] == expected


def test_mem0_disables_after_first_client_build_failure(tmp_path):
    # mem0 builds its client lazily on first .search()/.add() (the undeclared
    # `ollama` pip package + interactive input() prompt bug). Once that build
    # fails, subsequent calls must short-circuit to empty/no-op instead of
    # re-attempting the same broken (and here, expensive-to-detect) build.
    from mslearn.memory.mem0_impl import Mem0Memory
    from mslearn.opsdb import OpsDB
    from mslearn.settings import Settings

    settings = Settings(profiles_path=Path("profiles.yaml"))
    db = OpsDB(tmp_path / "ops.db")
    memory = Mem0Memory(settings, db)

    def _boom():
        raise RuntimeError("ollama not installed")

    memory._ensure_client = _boom  # noqa: SLF001 — simulate the lazy client build failing

    import pytest

    with pytest.raises(RuntimeError):
        memory.search("anything")
    assert memory._disabled is True

    # Second call short-circuits: no attempt to rebuild, no exception.
    assert memory.search("anything") == []
    assert memory.add("text", "interaction") == ""
    assert memory.all() == []
    memory.delete("some-id")  # no-op, must not raise


def test_mem0_embedder_model_honors_opsdb_override(tmp_path):
    from mslearn.memory.mem0_impl import Mem0Memory
    from mslearn.opsdb import OpsDB
    from mslearn.settings import Settings

    settings = Settings(profiles_path=Path("profiles.yaml"))
    db = OpsDB(tmp_path / "ops.db")
    db.set_setting("memory.embed_model", "custom-embed-model")

    config = Mem0Memory(settings, db)._build_config()
    assert config["embedder"]["config"]["model"] == "custom-embed-model"
