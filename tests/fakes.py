from __future__ import annotations

import math

from mslearn.graph.records import ConceptRecord, validate_classification


class InMemoryGraphStore:
    def __init__(
        self,
        *,
        spine_seq: dict[str, int] | None = None,
        concept_first_seq: dict[str, int] | None = None,
    ):
        self.claims: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.claim_to_concept: dict[str, str] = {}
        self.conflicts: dict[tuple[str, str], dict] = {}
        self.depends_on: set[tuple[str, str]] = set()
        self.spine_seq = dict(spine_seq or {})
        self._concept_first_seq = dict(concept_first_seq or {})

    def add_claim(
        self,
        claim_id: str,
        text: str,
        stance: str,
        source_id: str,
        embedding: list[float],
        trust: str = "trusted",
        spine_seq: int | None = None,
    ) -> None:
        self.claims[claim_id] = {
            "claim_id": claim_id,
            "text": text,
            "stance": stance,
            "source_id": source_id,
            "embedding": list(embedding),
            "trust": trust,
        }
        if spine_seq is not None:
            self.spine_seq[claim_id] = int(spine_seq)

    def unassigned_trusted_claims(self) -> list[dict]:
        rows = [
            {
                "claim_id": c["claim_id"],
                "text": c["text"],
                "stance": c["stance"],
                "source_id": c["source_id"],
                "embedding": list(c["embedding"]),
            }
            for c in self.claims.values()
            if c["trust"] in {"trusted", "escalated"} and c["claim_id"] not in self.claim_to_concept
        ]
        return sorted(rows, key=lambda r: r["claim_id"])

    def concept_id_of_claim(self, claim_id: str) -> str | None:
        return self.claim_to_concept.get(claim_id)

    def upsert_concept(self, concept: ConceptRecord) -> None:
        current = self.concepts.get(concept.concept_id, {})
        self.concepts[concept.concept_id] = {
            "concept_id": concept.concept_id,
            "name": concept.name,
            "summary": concept.summary,
            "order_index": current.get("order_index"),
            "dirty": current.get("dirty", False),
        }

    def assign_claim(self, claim_id: str, concept_id: str) -> None:
        self.claim_to_concept[claim_id] = concept_id

    def mark_concept_dirty(self, concept_id: str, dirty: bool = True) -> None:
        if concept_id not in self.concepts:
            return
        self.concepts[concept_id]["dirty"] = bool(dirty)

    def dirty_concepts(self) -> list[str]:
        return sorted(
            concept_id for concept_id, c in self.concepts.items() if c.get("dirty", False)
        )

    def claims_in_concept(self, concept_id: str) -> list[dict]:
        rows = []
        for claim_id, cid in self.claim_to_concept.items():
            if cid != concept_id or claim_id not in self.claims:
                continue
            claim = self.claims[claim_id]
            rows.append(
                {
                    "claim_id": claim_id,
                    "text": claim["text"],
                    "stance": claim["stance"],
                    "trust": claim["trust"],
                    "source_id": claim["source_id"],
                }
            )
        return sorted(rows, key=lambda r: r["claim_id"])

    def add_conflict(
        self, claim_a: str, claim_b: str, classification: str, rationale: str
    ) -> None:
        validate_classification(classification)
        claim_a, claim_b = sorted((claim_a, claim_b))
        self.conflicts[(claim_a, claim_b)] = {
            "claim_a": claim_a,
            "claim_b": claim_b,
            "classification": classification,
            "rationale": rationale,
        }

    def conflicts_in_concept(self, concept_id: str) -> list[dict]:
        members = {r["claim_id"] for r in self.claims_in_concept(concept_id)}
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
    ) -> None:
        if concept_id not in self.concepts:
            return
        if name is not None:
            self.concepts[concept_id]["name"] = name
        if summary is not None:
            self.concepts[concept_id]["summary"] = summary
        if order_index is not None:
            self.concepts[concept_id]["order_index"] = int(order_index)

    def all_concepts(self) -> list[dict]:
        return [
            {
                "concept_id": c["concept_id"],
                "name": c.get("name", ""),
                "summary": c.get("summary", ""),
                "order_index": c.get("order_index"),
                "dirty": c.get("dirty", False),
            }
            for _cid, c in sorted(self.concepts.items())
        ]

    def spine_concept_order(self) -> list[dict]:
        rows: list[dict] = []
        for concept_id in sorted(self.concepts):
            if concept_id in self._concept_first_seq:
                rows.append(
                    {"concept_id": concept_id, "first_seq": self._concept_first_seq[concept_id]}
                )
                continue
            seqs = [
                self.spine_seq[claim_id]
                for claim_id, cid in self.claim_to_concept.items()
                if cid == concept_id and claim_id in self.spine_seq
            ]
            if seqs:
                rows.append({"concept_id": concept_id, "first_seq": min(seqs)})
        return sorted(rows, key=lambda r: (r["first_seq"], r["concept_id"]))

    def add_depends_on(self, from_concept_id: str, to_concept_id: str) -> None:
        self.depends_on.add((from_concept_id, to_concept_id))

    def concept_dependencies(self) -> list[dict]:
        return [
            {"from_id": a, "to_id": b}
            for a, b in sorted(self.depends_on, key=lambda x: (x[0], x[1]))
        ]

    def curriculum(self) -> list[dict]:
        rows = [
            {
                "concept_id": c["concept_id"],
                "name": c.get("name", ""),
                "summary": c.get("summary", ""),
                "order_index": c.get("order_index"),
            }
            for c in self.concepts.values()
            if c.get("order_index") is not None
        ]
        return sorted(rows, key=lambda r: (r["order_index"], r["concept_id"]))

    def vector_search_claims(
        self, embedding: list[float], k: int, include_embedding: bool = False
    ) -> list[dict]:
        rows = []
        for claim in self.claims.values():
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


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
