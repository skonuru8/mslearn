from pathlib import Path

import yaml


def compose():
    return yaml.safe_load(Path("docker-compose.yml").read_text())


def test_redis_service_defined():
    svc = compose()["services"]["redis"]
    assert svc["image"].startswith("redis:7")
    assert "6379:6379" in svc["ports"]


def test_neo4j_service_defined_with_apoc():
    svc = compose()["services"]["neo4j"]
    assert svc["image"].startswith("neo4j:5")
    assert "7687:7687" in svc["ports"] and "7474:7474" in svc["ports"]
    env = svc["environment"]
    assert env["NEO4J_PLUGINS"] == '["apoc"]'
    assert "NEO4J_AUTH" in env


def test_neo4j_data_is_persisted_in_volume():
    svc = compose()["services"]["neo4j"]
    assert any("/data" in v for v in svc["volumes"])
