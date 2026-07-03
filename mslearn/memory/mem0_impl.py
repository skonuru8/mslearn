from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from mslearn.memory.base import MemoryItem
from mslearn.profiles import get_active_profile_name, load_profiles

if TYPE_CHECKING:
    from mslearn.opsdb import OpsDB
    from mslearn.settings import Settings

logger = logging.getLogger(__name__)

_USER_ID = "learner"


class Mem0Memory:
    def __init__(self, settings: Settings, db: OpsDB) -> None:
        self._settings = settings
        self._db = db
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from mem0 import Memory  # noqa: PLC0415 — lazy import gate

        self._client = Memory.from_config(self._build_config())
        return self._client

    def _build_config(self) -> dict[str, Any]:
        profiles = load_profiles(self._settings.profiles_path)
        active = get_active_profile_name(self._db, profiles)
        interactive_model = profiles.profiles[active].roles["interactive"].model
        llm_model = self._db.get_setting("memory.llm_model", interactive_model) or interactive_model

        return {
            "graph_store": {
                "provider": "neo4j",
                "config": {
                    "url": self._settings.neo4j_uri,
                    "username": self._settings.neo4j_user,
                    "password": self._settings.neo4j_password,
                },
            },
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model": "nomic-embed-text",
                    "ollama_base_url": self._settings.ollama_base_url,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": llm_model,
                    "api_key": self._settings.openrouter_api_key,
                    "openrouter_base_url": "https://openrouter.ai/api/v1",
                },
            },
        }

    @staticmethod
    def _to_item(raw: dict[str, Any]) -> MemoryItem:
        metadata = raw.get("metadata") or {}
        category = metadata.get("category", "interaction")
        created_at = _parse_created_at(raw.get("created_at"))
        return MemoryItem(
            memory_id=str(raw.get("id", "")),
            text=str(raw.get("memory", "")),
            category=str(category),
            created_at=created_at,
        )

    def add(self, text: str, category: str) -> str:
        result = self._ensure_client().add(
            text,
            user_id=_USER_ID,
            metadata={"category": category},
        )
        rows = result.get("results") if isinstance(result, dict) else result
        if isinstance(rows, dict):
            rows = rows.get("results", [])
        if not rows:
            raise RuntimeError("mem0 add returned no results")
        first = rows[0] if isinstance(rows, list) else rows
        if isinstance(first, dict):
            return str(first.get("id", ""))
        return str(first)

    def search(self, query: str, k: int = 5) -> list[MemoryItem]:
        result = self._ensure_client().search(
            query,
            top_k=k,
            filters={"user_id": _USER_ID},
        )
        rows = result.get("results", []) if isinstance(result, dict) else result
        return [self._to_item(row) for row in rows]

    def all(self) -> list[MemoryItem]:
        result = self._ensure_client().get_all(
            filters={"user_id": _USER_ID},
            top_k=1000,
        )
        rows = result.get("results", []) if isinstance(result, dict) else result
        return [self._to_item(row) for row in rows]

    def delete(self, memory_id: str) -> None:
        self._ensure_client().delete(memory_id)


def _parse_created_at(value: Any) -> float:
    if value is None:
        return datetime.now(tz=timezone.utc).timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return datetime.now(tz=timezone.utc).timestamp()
    return datetime.now(tz=timezone.utc).timestamp()
