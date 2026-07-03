from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    text: str
    category: str  # "struggle" | "preference" | "interaction"
    created_at: float


class LearnerMemory(Protocol):
    def add(self, text: str, category: str) -> str: ...
    def search(self, query: str, k: int = 5) -> list[MemoryItem]: ...
    def all(self) -> list[MemoryItem]: ...
    def delete(self, memory_id: str) -> None: ...
