from __future__ import annotations

import math
import time
import uuid

from mslearn.providers.base import ModelResponse
from mslearn.graph.records import ConceptRecord, validate_classification
from mslearn.memory.base import MemoryItem


class ScriptedRouter:
    def __init__(self, outputs=None, *, embeddings=None, stream_chunks=None):
        self.outputs = list(outputs or [])
        self.embeddings = list(embeddings or [])
        self.stream_chunks = list(stream_chunks or [])
        self.calls = []
        self.requests = []
        self.embed_texts = []

    def complete(self, role, request):
        self.calls.append(role)
        self.requests.append(request)
        out = self.outputs.pop(0)
        return ModelResponse(
            text=out if isinstance(out, str) else "",
            parsed=out if isinstance(out, dict) else None,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            provider="fake",
            model="m",
        )

    def stream(self, role, request):
        self.calls.append(role)
        self.requests.append(request)
        yield from self.stream_chunks

    def embed(self, texts):
        self.embed_texts.extend(texts)
        if self.embeddings:
            return [self.embeddings.pop(0) for _ in texts]
        return [[1.0, 0.0] for _ in texts]


class InMemoryGraphStore:
    def __init__(
        self,
        *,
        spine_seq: dict[str, int] | None = None,
        concept_first_seq: dict[str, int] | None = None,
    ):
        self.deleted_projects: list[str] = []
        self.claims: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.claim_to_concept: dict[str, str] = {}
        self.conflicts: dict[tuple[str, str], dict] = {}
        self.depends_on: set[tuple[str, str]] = set()
        self.spine_seq = dict(spine_seq or {})
        self._concept_first_seq = dict(concept_first_seq or {})
        self.sources: dict[str, dict] = {}
        self.chunks: dict[str, dict] = {}

    def ping(self) -> None:
        """No-op: the fake graph store is always "reachable"."""

    def upsert_source(self, doc, *, project_id: str = "default") -> None:
        self.sources[doc.source_id] = {
            "source_id": doc.source_id,
            "source_type": doc.source_type,
            "role": doc.role,
            "title": doc.title,
            "project_id": project_id,
        }

    def upsert_chunks(self, chunks, embeddings, *, project_id: str = "default") -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"embeddings length {len(embeddings)} != chunks length {len(chunks)}"
            )
        for chunk, embedding in zip(chunks, embeddings):
            self.chunks[chunk.chunk_id] = {
                "chunk_id": chunk.chunk_id,
                "source_id": chunk.source_id,
                "seq": chunk.seq,
                "unit_index": chunk.unit_index,
                "text": chunk.text,
                "embedding": list(embedding),
                "kind": chunk.locator.kind,
                "page": chunk.locator.page,
                "href": chunk.locator.href,
                "url": chunk.locator.url,
                "para_index": chunk.locator.para_index,
                "start_s": chunk.locator.start_s,
                "end_s": chunk.locator.end_s,
                "project_id": project_id,
            }

    def get_chunk(self, chunk_id: str, *, project_id: str = "default") -> dict | None:
        row = self.chunks.get(chunk_id)
        if row is None or row.get("project_id", "default") != project_id:
            return None
        return {k: v for k, v in row.items() if k != "embedding"}

    def source_type_of(self, source_id: str, *, project_id: str = "default") -> str | None:
        row = self.sources.get(source_id)
        if row is None or row.get("project_id", "default") != project_id:
            return None
        return row.get("source_type")

    def delete_source(self, source_id: str, *, project_id: str = "default") -> list[str]:
        doomed_claims = [
            cid
            for cid, row in self.claims.items()
            if row.get("source_id") == source_id
            and row.get("project_id", "default") == project_id
        ]
        affected = sorted(
            {
                self.claim_to_concept[cid]
                for cid in doomed_claims
                if cid in self.claim_to_concept
            }
        )
        for cid in doomed_claims:
            self.claims.pop(cid, None)
            self.claim_to_concept.pop(cid, None)
        self.chunks = {
            k: v
            for k, v in self.chunks.items()
            if not (
                v.get("source_id") == source_id
                and v.get("project_id", "default") == project_id
            )
        }
        self.sources.pop(source_id, None)
        for concept_id in affected:
            if concept_id not in self.claim_to_concept.values():
                self.concepts.pop(concept_id, None)
            elif concept_id in self.concepts:
                self.concepts[concept_id]["dirty"] = True
                self.concepts[concept_id]["teach_md"] = ""
        return affected

    def sample_chunks(self, limit: int = 50, *, project_id: str = "default") -> list[dict]:
        rows = [
            row
            for row in self.chunks.values()
            if row.get("project_id", "default") == project_id
        ][:limit]
        return [
            {
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "kind": row.get("kind"),
                "source_id": row["source_id"],
                "source_type": self.sources.get(row["source_id"], {}).get("source_type", "pdf"),
            }
            for row in rows
        ]

    def add_claim(
        self,
        claim_id: str,
        text: str,
        stance: str,
        source_id: str,
        embedding: list[float],
        trust: str = "trusted",
        spine_seq: int | None = None,
        quote: str = "",
        chunk_id: str | None = None,
        project_id: str = "default",
    ) -> None:
        self.claims[claim_id] = {
            "claim_id": claim_id,
            "text": text,
            "stance": stance,
            "source_id": source_id,
            "embedding": list(embedding),
            "trust": trust,
            "quote": quote,
            "chunk_id": chunk_id,
            "project_id": project_id,
        }
        if spine_seq is not None:
            self.spine_seq[claim_id] = int(spine_seq)

    def unassigned_trusted_claims(self, *, project_id: str = "default") -> list[dict]:
        rows = [
            {
                "claim_id": c["claim_id"],
                "text": c["text"],
                "stance": c["stance"],
                "source_id": c["source_id"],
                "embedding": list(c["embedding"]),
            }
            for c in self.claims.values()
            if c.get("project_id", "default") == project_id
            and c["trust"] in {"trusted", "escalated"} and c["claim_id"] not in self.claim_to_concept
        ]
        return sorted(rows, key=lambda r: r["claim_id"])

    def concept_id_of_claim(self, claim_id: str, *, project_id: str = "default") -> str | None:
        claim = self.claims.get(claim_id)
        concept_id = self.claim_to_concept.get(claim_id)
        concept = self.concepts.get(concept_id or "")
        if (
            claim is None
            or concept is None
            or claim.get("project_id", "default") != project_id
            or concept.get("project_id", "default") != project_id
        ):
            return None
        return concept_id

    def set_claim_trust(self, claim_id: str, trust: str, *, project_id: str = "default") -> None:
        if claim_id in self.claims and self.claims[claim_id].get("project_id", "default") == project_id:
            self.claims[claim_id]["trust"] = trust

    def upsert_concept(self, concept: ConceptRecord, *, project_id: str = "default") -> None:
        current = self.concepts.get(concept.concept_id, {})
        self.concepts[concept.concept_id] = {
            "concept_id": concept.concept_id,
            "name": concept.name,
            "summary": concept.summary,
            "order_index": current.get("order_index"),
            "dirty": current.get("dirty", False),
            "teach_md": current.get("teach_md", ""),
            "teach_at": current.get("teach_at"),
            "project_id": project_id,
        }

    def assign_claim(self, claim_id: str, concept_id: str, *, project_id: str = "default") -> None:
        if self.claims.get(claim_id, {}).get("project_id", "default") != project_id:
            return
        if self.concepts.get(concept_id, {}).get("project_id", "default") != project_id:
            return
        self.claim_to_concept[claim_id] = concept_id

    def mark_concept_dirty(
        self, concept_id: str, dirty: bool = True, *, project_id: str = "default"
    ) -> None:
        if (
            concept_id not in self.concepts
            or self.concepts[concept_id].get("project_id", "default") != project_id
        ):
            return
        self.concepts[concept_id]["dirty"] = bool(dirty)

    def dirty_concepts(self, *, project_id: str = "default") -> list[str]:
        return sorted(
            concept_id
            for concept_id, c in self.concepts.items()
            if c.get("project_id", "default") == project_id and c.get("dirty", False)
        )

    def claims_in_concept(self, concept_id: str, *, project_id: str = "default") -> list[dict]:
        rows = []
        concept = self.concepts.get(concept_id)
        if concept is None or concept.get("project_id", "default") != project_id:
            return rows
        for claim_id, cid in self.claim_to_concept.items():
            if cid != concept_id or claim_id not in self.claims:
                continue
            claim = self.claims[claim_id]
            if claim.get("project_id", "default") != project_id:
                continue
            rows.append(
                {
                    "claim_id": claim_id,
                    "text": claim["text"],
                    "stance": claim["stance"],
                    "trust": claim["trust"],
                    "source_id": claim["source_id"],
                    "quote": claim.get("quote", ""),
                    "chunk_id": claim.get("chunk_id"),
                }
            )
        return sorted(rows, key=lambda r: r["claim_id"])

    def add_conflict(
        self,
        claim_a: str,
        claim_b: str,
        classification: str,
        rationale: str,
        *,
        project_id: str = "default",
    ) -> None:
        validate_classification(classification)
        if self.claims.get(claim_a, {}).get("project_id", "default") != project_id:
            return
        if self.claims.get(claim_b, {}).get("project_id", "default") != project_id:
            return
        claim_a, claim_b = sorted((claim_a, claim_b))
        self.conflicts[(claim_a, claim_b)] = {
            "claim_a": claim_a,
            "claim_b": claim_b,
            "classification": classification,
            "rationale": rationale,
        }

    def conflicts_in_concept(self, concept_id: str, *, project_id: str = "default") -> list[dict]:
        members = {r["claim_id"] for r in self.claims_in_concept(concept_id, project_id=project_id)}
        rows = [
            dict(v)
            for v in self.conflicts.values()
            if v["claim_a"] in members and v["claim_b"] in members
        ]
        return sorted(rows, key=lambda r: (r["claim_a"], r["claim_b"]))

    def set_concept_meta(
        self,
        concept_id: str,
        name: str | None = None,
        summary: str | None = None,
        order_index: int | None = None,
        *,
        project_id: str = "default",
    ) -> None:
        if (
            concept_id not in self.concepts
            or self.concepts[concept_id].get("project_id", "default") != project_id
        ):
            return
        if name is not None:
            self.concepts[concept_id]["name"] = name
        if summary is not None:
            self.concepts[concept_id]["summary"] = summary
        if order_index is not None:
            self.concepts[concept_id]["order_index"] = int(order_index)

    def get_concept(self, concept_id: str, *, project_id: str = "default") -> dict | None:
        concept = self.concepts.get(concept_id)
        if concept is not None and concept.get("project_id", "default") != project_id:
            return None
        return dict(concept) if concept is not None else None

    def set_concept_teaching(
        self, concept_id: str, teach_md: str, *, project_id: str = "default"
    ) -> None:
        if (
            concept_id not in self.concepts
            or self.concepts[concept_id].get("project_id", "default") != project_id
        ):
            return
        self.concepts[concept_id]["teach_md"] = teach_md
        self.concepts[concept_id]["teach_at"] = time.time() if teach_md else None

    def citations_for_claims(
        self, claim_ids: list[str], *, project_id: str = "default"
    ) -> list[dict]:
        rows = []
        for claim_id in claim_ids:
            claim = self.claims.get(claim_id)
            if claim is None or claim.get("project_id", "default") != project_id:
                continue
            chunk = self.chunks.get(claim.get("chunk_id"))
            if chunk is None or chunk.get("project_id", "default") != project_id:
                continue
            rows.append(
                {
                    "claim_id": claim_id,
                    "chunk_id": chunk.get("chunk_id"),
                    "source_id": chunk.get("source_id"),
                    "seq": chunk.get("seq"),
                    "unit_index": chunk.get("unit_index"),
                    "kind": chunk.get("kind"),
                    "page": chunk.get("page"),
                    "href": chunk.get("href"),
                    "url": chunk.get("url"),
                    "para_index": chunk.get("para_index"),
                    "start_s": chunk.get("start_s"),
                    "end_s": chunk.get("end_s"),
                }
            )
        return rows

    def all_concepts(self, *, project_id: str = "default") -> list[dict]:
        return [
            {
                "concept_id": c["concept_id"],
                "name": c.get("name", ""),
                "summary": c.get("summary", ""),
                "order_index": c.get("order_index"),
                "dirty": c.get("dirty", False),
            }
            for _cid, c in sorted(self.concepts.items())
            if c.get("project_id", "default") == project_id
        ]

    def spine_concept_order(self, *, project_id: str = "default") -> list[dict]:
        rows: list[dict] = []
        for concept_id in sorted(self.concepts):
            if self.concepts[concept_id].get("project_id", "default") != project_id:
                continue
            if concept_id in self._concept_first_seq:
                rows.append(
                    {"concept_id": concept_id, "first_seq": self._concept_first_seq[concept_id]}
                )
                continue
            seqs = [
                self.spine_seq[claim_id]
                for claim_id, cid in self.claim_to_concept.items()
                if cid == concept_id
                and claim_id in self.spine_seq
                and self.claims.get(claim_id, {}).get("project_id", "default") == project_id
            ]
            if seqs:
                rows.append({"concept_id": concept_id, "first_seq": min(seqs)})
        return sorted(rows, key=lambda r: (r["first_seq"], r["concept_id"]))

    def add_depends_on(
        self, from_concept_id: str, to_concept_id: str, *, project_id: str = "default"
    ) -> None:
        if self.concepts.get(from_concept_id, {}).get("project_id", "default") != project_id:
            return
        if self.concepts.get(to_concept_id, {}).get("project_id", "default") != project_id:
            return
        self.depends_on.add((from_concept_id, to_concept_id))

    def concept_dependencies(self, *, project_id: str = "default") -> list[dict]:
        return [
            {"from_id": a, "to_id": b}
            for a, b in sorted(self.depends_on, key=lambda x: (x[0], x[1]))
            if self.concepts.get(a, {}).get("project_id", "default") == project_id
            and self.concepts.get(b, {}).get("project_id", "default") == project_id
        ]

    def curriculum(self, *, project_id: str = "default") -> list[dict]:
        rows = [
            {
                "concept_id": c["concept_id"],
                "name": c.get("name", ""),
                "summary": c.get("summary", ""),
                "order_index": c.get("order_index"),
                "conflict_count": sum(
                    1
                    for (a, b) in self.conflicts
                    if self.claim_to_concept.get(a) == c["concept_id"]
                    and self.claim_to_concept.get(b) == c["concept_id"]
                ),
            }
            for c in self.concepts.values()
            if c.get("project_id", "default") == project_id and c.get("order_index") is not None
        ]
        return sorted(rows, key=lambda r: (r["order_index"], r["concept_id"]))

    def vector_search_claims(
        self,
        embedding: list[float],
        k: int,
        include_embedding: bool = False,
        *,
        project_id: str = "default",
    ) -> list[dict]:
        rows = []
        for claim in self.claims.values():
            if claim.get("project_id", "default") != project_id:
                continue
            score = _cosine(embedding, claim["embedding"])
            row = {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "stance": claim["stance"],
                "source_id": claim["source_id"],
                "trust": claim["trust"],
                "score": score,
            }
            if include_embedding:
                row["embedding"] = list(claim["embedding"])
            rows.append(row)
        rows.sort(key=lambda r: (-r["score"], r["claim_id"]))
        return rows[:k]

    def vector_search_chunks(
        self,
        embedding: list[float],
        k: int,
        include_embedding: bool = False,
        *,
        project_id: str = "default",
    ) -> list[dict]:
        rows = []
        for chunk in self.chunks.values():
            if chunk.get("project_id", "default") != project_id:
                continue
            score = _cosine(embedding, chunk.get("embedding", [0.0] * len(embedding)))
            row = {k: v for k, v in chunk.items() if include_embedding or k != "embedding"}
            row["score"] = score
            rows.append(row)
        rows.sort(key=lambda r: (-r["score"], r["chunk_id"]))
        return rows[:k]

    def delete_project(self, project_id: str) -> None:
        self.deleted_projects.append(project_id)
        self.sources = {
            k: v for k, v in self.sources.items() if v.get("project_id", "default") != project_id
        }
        self.chunks = {
            k: v for k, v in self.chunks.items() if v.get("project_id", "default") != project_id
        }
        self.claims = {
            k: v for k, v in self.claims.items() if v.get("project_id", "default") != project_id
        }
        self.concepts = {
            k: v for k, v in self.concepts.items() if v.get("project_id", "default") != project_id
        }
        self.claim_to_concept = {
            cid: k for cid, k in self.claim_to_concept.items() if cid in self.claims
        }
        self.conflicts = {
            k: v for k, v in self.conflicts.items() if k[0] in self.claims and k[1] in self.claims
        }
        self.depends_on = {
            e for e in self.depends_on if e[0] in self.concepts and e[1] in self.concepts
        }

    def export_all(self, *, project_id: str = "default") -> tuple[list[dict], list[dict]]:
        nodes = [
            {
                "id": f"concept:{concept_id}",
                "labels": ["Concept"],
                "properties": {
                    "concept_id": concept_id,
                    "name": concept.get("name", ""),
                    "summary": concept.get("summary", ""),
                },
            }
            for concept_id, concept in sorted(self.concepts.items())
            if concept.get("project_id", "default") == project_id
        ]
        nodes.extend(
            {
                "id": f"claim:{claim_id}",
                "labels": ["Claim"],
                "properties": {
                    "claim_id": claim_id,
                    "text": claim.get("text", ""),
                    "stance": claim.get("stance", ""),
                    "source_id": claim.get("source_id", ""),
                },
            }
            for claim_id, claim in sorted(self.claims.items())
            if claim.get("project_id", "default") == project_id
        )
        rels = [
            {
                "start": f"claim:{claim_id}",
                "end": f"concept:{concept_id}",
                "type": "IN_CONCEPT",
                "properties": {},
            }
            for claim_id, concept_id in sorted(self.claim_to_concept.items())
            if claim_id in self.claims
            and concept_id in self.concepts
            and self.claims[claim_id].get("project_id", "default") == project_id
            and self.concepts[concept_id].get("project_id", "default") == project_id
        ]
        return nodes, rels


class InMemoryLearnerMemory:
    def __init__(self) -> None:
        self._items: list[MemoryItem] = []
        self._project_ids: dict[str, str] = {}

    def add(self, text: str, category: str, project_id: str = "default") -> str:
        memory_id = str(uuid.uuid4())
        self._items.append(
            MemoryItem(
                memory_id=memory_id,
                text=text,
                category=category,
                created_at=time.time(),
            )
        )
        self._project_ids[memory_id] = project_id
        return memory_id

    def search(self, query: str, k: int = 5, project_id: str = "default") -> list[MemoryItem]:
        needle = query.lower()
        hits = [
            item
            for item in self._items
            if self._project_ids.get(item.memory_id, "default") == project_id
            and needle in item.text.lower()
        ]
        return hits[:k]

    def all(self, project_id: str = "default") -> list[MemoryItem]:
        return [
            item
            for item in self._items
            if self._project_ids.get(item.memory_id, "default") == project_id
        ]

    def delete(self, memory_id: str) -> None:
        self._items = [item for item in self._items if item.memory_id != memory_id]
        self._project_ids.pop(memory_id, None)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
