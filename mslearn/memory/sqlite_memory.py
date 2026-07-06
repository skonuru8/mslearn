from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

from mslearn.memory.base import MemoryItem
from mslearn.pipeline.trust import cosine

if TYPE_CHECKING:
    from mslearn.opsdb import OpsDB
    from mslearn.providers.router import ModelRouter

logger = logging.getLogger(__name__)


class SqliteMemory:
    """In-house `LearnerMemory` backend (Plan 16).

    Stores personalization-only text (interaction/struggle/preference notes,
    never facts — spec §3b) in OpsDB's `learner_memory` table, embedding it
    through the app's existing `ModelRouter.embed` path: plain httpx to
    Ollama's `/api/embed`, with no dependency on any third-party memory
    library or its optional pip extras. `search` does cosine similarity in
    Python over this project's rows — learner memory is small (dozens to low
    hundreds of rows), so no vector index is needed.
    """

    def __init__(self, db: OpsDB, router: ModelRouter) -> None:
        self._db = db
        self._router = router
        # Set on first embedding/storage failure. Once broken, short-circuit
        # to a no-op instead of re-attempting the same failing embed call on
        # every request — memory is advisory/personalization-only and must
        # never break an interactive endpoint.
        self._disabled = False

    def add(self, text: str, category: str, project_id: str = "default") -> str:
        if self._disabled:
            return ""
        try:
            embedding = self._router.embed([text])[0]
            memory_id = str(uuid.uuid4())
            self._db.add_memory_item(
                memory_id=memory_id,
                project_id=project_id,
                category=category,
                text=text,
                embedding_json=json.dumps(list(embedding)),
                created_ts=time.time(),
            )
            return memory_id
        except Exception:
            self._disabled = True
            raise

    def search(self, query: str, k: int = 5, project_id: str = "default") -> list[MemoryItem]:
        if self._disabled:
            return []
        try:
            rows = self._db.memory_items(project_id=project_id)
            if not rows:
                return []
            query_embedding = self._router.embed([query])[0]
            scored = [
                (cosine(query_embedding, json.loads(row["embedding"])), row) for row in rows
            ]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return [_to_item(row) for _, row in scored[:k]]
        except Exception:
            self._disabled = True
            raise

    def all(self, project_id: str = "default") -> list[MemoryItem]:
        return [_to_item(row) for row in self._db.memory_items(project_id=project_id)]

    def delete(self, memory_id: str) -> None:
        self._db.delete_memory_item(memory_id)


def _to_item(row: dict) -> MemoryItem:
    return MemoryItem(
        memory_id=row["memory_id"],
        text=row["text"],
        category=row["category"],
        created_at=row["created_ts"],
    )
