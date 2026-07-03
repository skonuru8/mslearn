from pathlib import Path

from mslearn.settings import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.data_dir == Path("data")
    assert s.ops_db == Path("data") / "ops.db"
    assert s.redis_url == "redis://localhost:6380/0"
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.ollama_base_url == "http://localhost:11434"


def test_env_override(monkeypatch):
    monkeypatch.setenv("MSL_OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("MSL_DATA_DIR", "/tmp/msl")
    s = Settings(_env_file=None)
    assert s.openrouter_api_key == "sk-test"
    assert s.ops_db == Path("/tmp/msl/ops.db")
