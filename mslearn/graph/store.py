import time

from neo4j import GraphDatabase

from mslearn.graph.records import validate_classification

_CONSTRAINTS = [
    "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.source_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.claim_id IS UNIQUE",
    "CREATE CONSTRAINT concept_id IF NOT EXISTS FOR (n:Concept) REQUIRE n.concept_id IS UNIQUE",
]

_VECTOR_INDEX = (
    "CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
    "OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, "
    "`vector.similarity_function`: 'cosine'}}}}"
)


class GraphWriteError(Exception):
    """A graph write matched nothing and silently changed nothing."""


class GraphStore:
    def __init__(self, uri: str, user: str, password: str, embedding_dim: int = 768):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._dim = int(embedding_dim)

    # -- lifecycle -----------------------------------------------------
    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def ping(self) -> None:
        self._driver.verify_connectivity()

    # -- low-level helpers (used by later tasks and tests) --------------
    def run_write(self, query: str, **params):
        with self._driver.session() as session:
            return session.execute_write(
                lambda tx: tx.run(query, **params).consume().counters
            )

    def run_write_checked(self, query: str, **params):
        counters = self.run_write(query, **params)
        if not counters.contains_updates:
            raise GraphWriteError(f"write changed nothing: {query[:120]}")
        return counters

    def run_read(self, query: str, **params) -> list[dict]:
        with self._driver.session() as session:
            return session.execute_read(
                lambda tx: [dict(r) for r in tx.run(query, **params)]
            )

    # -- schema ----------------------------------------------------------
    def ensure_schema(self) -> None:
        for stmt in _CONSTRAINTS:
            self.run_write(stmt)
        for name, label in (("claim_embedding", "Claim"), ("chunk_embedding", "Chunk")):
            self.run_write(_VECTOR_INDEX.format(name=name, label=label, dim=self._dim))

    def list_index_names(self) -> list[str]:
        return [r["name"] for r in self.run_read("SHOW INDEXES YIELD name RETURN name")]

    # -- test/maintenance --------------------------------------------------
    def wipe(self) -> None:
        self.run_write("MATCH (n) DETACH DELETE n")

    def node_count(self) -> int:
        return self.run_read("MATCH (n) RETURN count(n) AS c")[0]["c"]

    # -- ingest -----------------------------------------------------------
    def upsert_source(self, doc) -> None:
        self.run_write(
            "MERGE (s:Source {source_id: $source_id}) "
            "SET s.source_type = $source_type, s.role = $role, s.title = $title",
            source_id=doc.source_id, source_type=doc.source_type,
            role=doc.role, title=doc.title,
        )

    def upsert_chunks(self, chunks, embeddings) -> None:
        """No-op if the Source node doesn't exist — caller must ensure it was upserted first."""
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"embeddings length {len(embeddings)} != chunks length {len(chunks)}"
            )
        rows = [
            {
                "chunk_id": c.chunk_id, "source_id": c.source_id, "seq": c.seq,
                "unit_index": c.unit_index, "text": c.text, "embedding": emb,
                "kind": c.locator.kind, "page": c.locator.page, "href": c.locator.href,
                "url": c.locator.url, "para_index": c.locator.para_index,
                "start_s": c.locator.start_s, "end_s": c.locator.end_s,
            }
            for c, emb in zip(chunks, embeddings)
        ]
        self.run_write(
            "UNWIND $rows AS row "
            "MATCH (s:Source {source_id: row.source_id}) "
            "MERGE (c:Chunk {chunk_id: row.chunk_id}) "
            "SET c += row "
            "MERGE (s)-[:HAS_CHUNK]->(c)",
            rows=rows,
        )

    def chunks_for_source(self, source_id: str) -> list[dict]:
        return self.run_read(
            "MATCH (:Source {source_id: $source_id})-[:HAS_CHUNK]->(c:Chunk) "
            "RETURN c.chunk_id AS chunk_id, c.seq AS seq, c.unit_index AS unit_index, "
            "c.text AS text, c.kind AS kind, c.page AS page, c.href AS href, "
            "c.url AS url, c.para_index AS para_index, c.start_s AS start_s, "
            "c.end_s AS end_s ORDER BY c.seq",
            source_id=source_id,
        )

    def get_chunk(self, chunk_id: str) -> dict | None:
        rows = self.run_read(
            "MATCH (c:Chunk {chunk_id: $chunk_id}) "
            "RETURN c.chunk_id AS chunk_id, c.source_id AS source_id, c.text AS text, "
            "c.seq AS seq, c.kind AS kind, c.page AS page, c.href AS href, c.url AS url, "
            "c.para_index AS para_index, c.start_s AS start_s, c.end_s AS end_s",
            chunk_id=chunk_id,
        )
        return rows[0] if rows else None

    def sample_chunks(self, limit: int = 50) -> list[dict]:
        return self.run_read(
            "MATCH (c:Chunk)<-[:HAS_CHUNK]-(s:Source) "
            "RETURN c.chunk_id AS chunk_id, c.text AS text, c.kind AS kind, "
            "s.source_id AS source_id, s.source_type AS source_type "
            "ORDER BY c.chunk_id LIMIT $limit",
            limit=int(limit),
        )

    # -- claims -----------------------------------------------------------
    def upsert_claim(self, claim, embedding: list[float]) -> None:
        """No-op if the Chunk node doesn't exist — caller must ensure it was upserted first."""
        self.run_write_checked(
            "MATCH (ch:Chunk {chunk_id: $chunk_id}) "
            "MERGE (c:Claim {claim_id: $claim_id}) "
            "SET c.text = $text, c.stance = $stance, c.quote = $quote, "
            "c.trust = $trust, c.source_id = $source_id, c.embedding = $embedding "
            "MERGE (c)-[:EXTRACTED_FROM]->(ch)",
            chunk_id=claim.chunk_id, claim_id=claim.claim_id, text=claim.text,
            stance=claim.stance, quote=claim.quote, trust=claim.trust,
            source_id=claim.source_id, embedding=embedding,
        )

    def claims_for_source(self, source_id: str) -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim {source_id: $source_id})-[:EXTRACTED_FROM]->(ch:Chunk) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.quote AS quote, c.trust AS trust, ch.chunk_id AS chunk_id "
            "ORDER BY c.claim_id",
            source_id=source_id,
        )

    def set_claim_trust(self, claim_id: str, trust: str) -> None:
        """No-op if the Claim node doesn't exist — caller must ensure it was upserted first."""
        self.run_write(
            "MATCH (c:Claim {claim_id: $claim_id}) SET c.trust = $trust",
            claim_id=claim_id, trust=trust,
        )

    def _vector_search(
        self, index: str, embedding: list[float], k: int, include_embedding: bool = False
    ) -> list[dict]:
        rows = self.run_read(
            f"CALL db.index.vector.queryNodes('{index}', $k, $embedding) "
            "YIELD node, score RETURN node{.*, score: score} AS hit",
            k=k, embedding=embedding,
        )
        hits = [r["hit"] for r in rows]
        if not include_embedding:
            for hit in hits:
                hit.pop("embedding", None)
        return hits

    def vector_search_claims(
        self, embedding: list[float], k: int = 10, include_embedding: bool = False
    ) -> list[dict]:
        return self._vector_search(
            "claim_embedding", embedding, k, include_embedding=include_embedding
        )

    def vector_search_chunks(
        self, embedding: list[float], k: int = 10, include_embedding: bool = False
    ) -> list[dict]:
        return self._vector_search(
            "chunk_embedding", embedding, k, include_embedding=include_embedding
        )

    # -- concepts -----------------------------------------------------------
    def upsert_concept(self, concept) -> None:
        self.run_write(
            "MERGE (k:Concept {concept_id: $concept_id}) "
            "SET k.name = $name, k.summary = $summary",
            concept_id=concept.concept_id, name=concept.name, summary=concept.summary,
        )

    def assign_claim(self, claim_id: str, concept_id: str) -> None:
        """No-op if Claim or Concept doesn't exist — caller must ensure they were upserted first."""
        self.run_write(
            "MATCH (c:Claim {claim_id: $claim_id}), (k:Concept {concept_id: $concept_id}) "
            "MERGE (c)-[:IN_CONCEPT]->(k)",
            claim_id=claim_id, concept_id=concept_id,
        )

    def add_depends_on(self, from_concept_id: str, to_concept_id: str) -> None:
        """No-op if either Concept doesn't exist — caller must ensure they were upserted first."""
        self.run_write(
            "MATCH (a:Concept {concept_id: $a}), (b:Concept {concept_id: $b}) "
            "MERGE (a)-[:DEPENDS_ON]->(b)",
            a=from_concept_id, b=to_concept_id,
        )

    def add_conflict(self, claim_a: str, claim_b: str,
                     classification: str, rationale: str) -> None:
        """Creates/updates normalized CONFLICTS_WITH edge for a claim pair."""
        validate_classification(classification)
        claim_a, claim_b = sorted((claim_a, claim_b))
        self.run_write(
            "MATCH (a:Claim {claim_id: $a}), (b:Claim {claim_id: $b}) "
            "MERGE (a)-[r:CONFLICTS_WITH]->(b) "
            "SET r.classification = $classification, r.rationale = $rationale",
            a=claim_a, b=claim_b, classification=classification, rationale=rationale,
        )

    def claims_in_concept(self, concept_id: str) -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim)-[:IN_CONCEPT]->(:Concept {concept_id: $concept_id}) "
            "OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(ch:Chunk) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.quote AS quote, c.trust AS trust, c.source_id AS source_id, "
            "ch.chunk_id AS chunk_id ORDER BY c.claim_id",
            concept_id=concept_id,
        )

    def conflicts_in_concept(self, concept_id: str) -> list[dict]:
        return self.run_read(
            "MATCH (a:Claim)-[r:CONFLICTS_WITH]->(b:Claim), "
            "(a)-[:IN_CONCEPT]->(k:Concept {concept_id: $concept_id}), "
            "(b)-[:IN_CONCEPT]->(k) "
            "RETURN a.claim_id AS claim_a, b.claim_id AS claim_b, "
            "r.classification AS classification, r.rationale AS rationale",
            concept_id=concept_id,
        )

    def concept_dependencies(self) -> list[dict]:
        return self.run_read(
            "MATCH (a:Concept)-[:DEPENDS_ON]->(b:Concept) "
            "RETURN a.concept_id AS from_id, b.concept_id AS to_id",
        )

    def mark_concept_dirty(self, concept_id: str, dirty: bool = True) -> None:
        """No-op if the Concept node doesn't exist — caller must ensure it was upserted first."""
        self.run_write(
            "MATCH (k:Concept {concept_id: $concept_id}) SET k.dirty = $dirty",
            concept_id=concept_id, dirty=dirty,
        )

    def dirty_concepts(self) -> list[str]:
        return [r["concept_id"] for r in self.run_read(
            "MATCH (k:Concept {dirty: true}) RETURN k.concept_id AS concept_id "
            "ORDER BY k.concept_id",
        )]

    def unassigned_trusted_claims(self) -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim) "
            "WHERE c.trust IN ['trusted', 'escalated'] "
            "AND NOT (c)-[:IN_CONCEPT]->(:Concept) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.source_id AS source_id, c.embedding AS embedding "
            "ORDER BY c.claim_id"
        )

    def concept_id_of_claim(self, claim_id: str) -> str | None:
        rows = self.run_read(
            "MATCH (:Claim {claim_id: $claim_id})-[:IN_CONCEPT]->(k:Concept) "
            "RETURN k.concept_id AS concept_id LIMIT 1",
            claim_id=claim_id,
        )
        return rows[0]["concept_id"] if rows else None

    def get_concept(self, concept_id: str) -> dict | None:
        rows = self.run_read(
            "MATCH (k:Concept {concept_id: $concept_id}) "
            "RETURN k.concept_id AS concept_id, k.name AS name, "
            "k.summary AS summary, k.order_index AS order_index, "
            "coalesce(k.dirty, false) AS dirty, coalesce(k.teach_md, '') AS teach_md, "
            "k.teach_at AS teach_at",
            concept_id=concept_id,
        )
        return rows[0] if rows else None

    def set_concept_teaching(self, concept_id: str, teach_md: str) -> None:
        teach_at = time.time() if teach_md else None
        self.run_write(
            "MATCH (k:Concept {concept_id: $concept_id}) "
            "SET k.teach_md = $teach_md, k.teach_at = $teach_at",
            concept_id=concept_id, teach_md=teach_md, teach_at=teach_at,
        )

    def citations_for_claims(self, claim_ids: list[str]) -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim)-[:EXTRACTED_FROM]->(ch:Chunk) "
            "WHERE c.claim_id IN $claim_ids "
            "RETURN c.claim_id AS claim_id, ch.chunk_id AS chunk_id, "
            "ch.source_id AS source_id, ch.seq AS seq, ch.unit_index AS unit_index, "
            "ch.kind AS kind, ch.page AS page, ch.href AS href, ch.url AS url, "
            "ch.para_index AS para_index, ch.start_s AS start_s, ch.end_s AS end_s "
            "ORDER BY c.claim_id",
            claim_ids=claim_ids,
        )

    def set_concept_meta(
        self,
        concept_id: str,
        name: str | None = None,
        summary: str | None = None,
        order_index: int | None = None,
    ) -> None:
        updates: list[str] = []
        params: dict = {"concept_id": concept_id}
        if name is not None:
            updates.append("k.name = $name")
            params["name"] = name
        if summary is not None:
            updates.append("k.summary = $summary")
            params["summary"] = summary
        if order_index is not None:
            updates.append("k.order_index = $order_index")
            params["order_index"] = int(order_index)
        if not updates:
            return
        self.run_write_checked(
            "MATCH (k:Concept {concept_id: $concept_id}) "
            f"SET {', '.join(updates)}",
            **params,
        )

    def all_concepts(self) -> list[dict]:
        return self.run_read(
            "MATCH (k:Concept) "
            "RETURN k.concept_id AS concept_id, k.name AS name, "
            "k.summary AS summary, k.order_index AS order_index, "
            "coalesce(k.dirty, false) AS dirty "
            "ORDER BY k.concept_id"
        )

    def spine_concept_order(self) -> list[dict]:
        return self.run_read(
            "MATCH (cl:Claim)-[:IN_CONCEPT]->(k:Concept), "
            "(cl)-[:EXTRACTED_FROM]->(ch:Chunk)<-[:HAS_CHUNK]-(s:Source {role:'spine'}) "
            "RETURN k.concept_id AS concept_id, min(ch.seq) AS first_seq "
            "ORDER BY first_seq"
        )

    def curriculum(self) -> list[dict]:
        return self.run_read(
            "MATCH (k:Concept) WHERE k.order_index IS NOT NULL "
            "RETURN k.concept_id AS concept_id, k.name AS name, "
            "k.summary AS summary, k.order_index AS order_index "
            "ORDER BY k.order_index"
        )

    # -- export -----------------------------------------------------------
    def export_all(self) -> tuple[list[dict], list[dict]]:
        nodes = [
            {
                "id": f"n{r['nid']}",
                "labels": r["labels"],
                "properties": {k: v for k, v in r["props"].items() if k != "embedding"},
            }
            for r in self.run_read(
                "MATCH (n) RETURN id(n) AS nid, labels(n) AS labels, properties(n) AS props"
            )
        ]
        rels = [
            {
                "start": f"n{r['start']}", "end": f"n{r['end']}",
                "type": r["type"], "properties": r["props"],
            }
            for r in self.run_read(
                "MATCH (a)-[r]->(b) RETURN id(a) AS start, id(b) AS end, "
                "type(r) AS type, properties(r) AS props"
            )
        ]
        return nodes, rels
