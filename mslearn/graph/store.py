import logging
import time

import neo4j
from neo4j import GraphDatabase

from mslearn.graph.records import validate_classification

# neo4j python driver >= 5.7 accepts notifications_min_severity to stop the
# driver from spamming stdout with GqlStatusObject warnings for every query
# that touches a label/property that doesn't exist yet (e.g. an empty graph).
# Harmless but drowns real logs. Fall back to suppressing the notifications
# logger if an older driver doesn't support the kwarg.
_SUPPORTS_MIN_SEVERITY = tuple(int(p) for p in neo4j.__version__.split(".")[:2]) >= (5, 7)
if not _SUPPORTS_MIN_SEVERITY:
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

_LEGACY_CONSTRAINTS = ["source_id", "chunk_id", "claim_id", "concept_id"]

_CONSTRAINTS = [
    (
        "CREATE CONSTRAINT source_project_id IF NOT EXISTS "
        "FOR (n:Source) REQUIRE (n.project_id, n.source_id) IS UNIQUE"
    ),
    (
        "CREATE CONSTRAINT chunk_project_id IF NOT EXISTS "
        "FOR (n:Chunk) REQUIRE (n.project_id, n.chunk_id) IS UNIQUE"
    ),
    (
        "CREATE CONSTRAINT claim_project_id IF NOT EXISTS "
        "FOR (n:Claim) REQUIRE (n.project_id, n.claim_id) IS UNIQUE"
    ),
    (
        "CREATE CONSTRAINT concept_project_id IF NOT EXISTS "
        "FOR (n:Concept) REQUIRE (n.project_id, n.concept_id) IS UNIQUE"
    ),
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
        driver_kwargs = {"notifications_min_severity": "OFF"} if _SUPPORTS_MIN_SEVERITY else {}
        self._driver = GraphDatabase.driver(uri, auth=(user, password), **driver_kwargs)
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
        for name in _LEGACY_CONSTRAINTS:
            self.run_write(f"DROP CONSTRAINT {name} IF EXISTS")
        for stmt in _CONSTRAINTS:
            self.run_write(stmt)
        for name, label in (("claim_embedding", "Claim"), ("chunk_embedding", "Chunk")):
            self.run_write(_VECTOR_INDEX.format(name=name, label=label, dim=self._dim))

    def list_index_names(self) -> list[str]:
        return [r["name"] for r in self.run_read("SHOW INDEXES YIELD name RETURN name")]

    # -- test/maintenance --------------------------------------------------
    def wipe(self) -> None:
        self.run_write("MATCH (n) DETACH DELETE n")

    def delete_project(self, project_id: str) -> None:
        self.run_write(
            "MATCH (n {project_id: $project_id}) DETACH DELETE n",
            project_id=project_id,
        )

    def node_count(self, *, project_id: str | None = None) -> int:
        if project_id is None:
            return self.run_read("MATCH (n) RETURN count(n) AS c")[0]["c"]
        return self.run_read(
            "MATCH (n {project_id: $project_id}) RETURN count(n) AS c",
            project_id=project_id,
        )[0]["c"]

    # -- ingest -----------------------------------------------------------
    def upsert_source(self, doc, *, project_id: str = "default") -> None:
        self.run_write(
            "MERGE (s:Source {source_id: $source_id, project_id: $project_id}) "
            "SET s.source_type = $source_type, s.role = $role, s.title = $title",
            source_id=doc.source_id,
            project_id=project_id,
            source_type=doc.source_type,
            role=doc.role,
            title=doc.title,
        )

    def upsert_chunks(self, chunks, embeddings, *, project_id: str = "default") -> None:
        """No-op if the Source node doesn't exist — caller must ensure it was upserted first."""
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"embeddings length {len(embeddings)} != chunks length {len(chunks)}"
            )
        rows = [
            {
                "chunk_id": c.chunk_id,
                "source_id": c.source_id,
                "seq": c.seq,
                "unit_index": c.unit_index,
                "text": c.text,
                "embedding": emb,
                "kind": c.locator.kind,
                "page": c.locator.page,
                "href": c.locator.href,
                "url": c.locator.url,
                "para_index": c.locator.para_index,
                "start_s": c.locator.start_s,
                "end_s": c.locator.end_s,
                "project_id": project_id,
            }
            for c, emb in zip(chunks, embeddings)
        ]
        self.run_write(
            "UNWIND $rows AS row "
            "MATCH (s:Source {source_id: row.source_id, project_id: row.project_id}) "
            "MERGE (c:Chunk {chunk_id: row.chunk_id, project_id: row.project_id}) "
            "SET c += row "
            "MERGE (s)-[:HAS_CHUNK]->(c)",
            rows=rows,
        )

    def chunks_for_source(self, source_id: str, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (:Source {source_id: $source_id, project_id: $project_id})"
            "-[:HAS_CHUNK]->(c:Chunk {project_id: $project_id}) "
            "RETURN c.chunk_id AS chunk_id, c.seq AS seq, c.unit_index AS unit_index, "
            "c.text AS text, c.kind AS kind, c.page AS page, c.href AS href, "
            "c.url AS url, c.para_index AS para_index, c.start_s AS start_s, "
            "c.end_s AS end_s ORDER BY c.seq",
            source_id=source_id,
            project_id=project_id,
        )

    def get_chunk(self, chunk_id: str, *, project_id: str = "default") -> dict | None:
        rows = self.run_read(
            "MATCH (c:Chunk {chunk_id: $chunk_id, project_id: $project_id}) "
            "RETURN c.chunk_id AS chunk_id, c.source_id AS source_id, c.text AS text, "
            "c.seq AS seq, c.kind AS kind, c.page AS page, c.href AS href, c.url AS url, "
            "c.para_index AS para_index, c.start_s AS start_s, c.end_s AS end_s",
            chunk_id=chunk_id,
            project_id=project_id,
        )
        return rows[0] if rows else None

    def source_type_of(self, source_id: str, *, project_id: str = "default") -> str | None:
        rows = self.run_read(
            "MATCH (s:Source {source_id: $source_id, project_id: $project_id}) "
            "RETURN s.source_type AS source_type",
            source_id=source_id,
            project_id=project_id,
        )
        return rows[0]["source_type"] if rows else None

    def sample_chunks(self, limit: int = 50, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (c:Chunk {project_id: $project_id})<-[:HAS_CHUNK]-(s:Source {project_id: $project_id}) "
            "RETURN c.chunk_id AS chunk_id, c.text AS text, c.kind AS kind, "
            "s.source_id AS source_id, s.source_type AS source_type "
            "ORDER BY c.chunk_id LIMIT $limit",
            limit=int(limit),
            project_id=project_id,
        )

    # -- claims -----------------------------------------------------------
    def upsert_claim(self, claim, embedding: list[float], *, project_id: str = "default") -> None:
        """No-op if the Chunk node doesn't exist — caller must ensure it was upserted first."""
        self.run_write_checked(
            "MATCH (ch:Chunk {chunk_id: $chunk_id, project_id: $project_id}) "
            "MERGE (c:Claim {claim_id: $claim_id, project_id: $project_id}) "
            "SET c.text = $text, c.stance = $stance, c.quote = $quote, "
            "c.trust = $trust, c.source_id = $source_id, c.embedding = $embedding, "
            "c.kind = $kind "
            "MERGE (c)-[:EXTRACTED_FROM]->(ch)",
            chunk_id=claim.chunk_id,
            project_id=project_id,
            claim_id=claim.claim_id,
            text=claim.text,
            stance=claim.stance,
            quote=claim.quote,
            trust=claim.trust,
            source_id=claim.source_id,
            embedding=embedding,
            kind=getattr(claim, "kind", "claim"),
        )

    def claims_for_source(self, source_id: str, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim {source_id: $source_id, project_id: $project_id})"
            "-[:EXTRACTED_FROM]->(ch:Chunk {project_id: $project_id}) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.quote AS quote, c.trust AS trust, c.kind AS kind, ch.chunk_id AS chunk_id "
            "ORDER BY c.claim_id",
            source_id=source_id,
            project_id=project_id,
        )

    def delete_source(self, source_id: str, *, project_id: str = "default") -> list[str]:
        """Delete a source with its chunks and claims.

        Concepts that lose claims are marked dirty with their cached teaching
        cleared; concepts left with no claims at all are deleted. Returns the
        affected concept ids (including ones that were deleted).
        """
        affected = [
            row["concept_id"]
            for row in self.run_read(
                "MATCH (cl:Claim {source_id: $source_id, project_id: $project_id})"
                "-[:IN_CONCEPT]->(k:Concept) RETURN DISTINCT k.concept_id AS concept_id",
                source_id=source_id,
                project_id=project_id,
            )
        ]
        self.run_write(
            "MATCH (cl:Claim {source_id: $source_id, project_id: $project_id})"
            " DETACH DELETE cl",
            source_id=source_id,
            project_id=project_id,
        )
        self.run_write(
            "MATCH (ch:Chunk {source_id: $source_id, project_id: $project_id})"
            " DETACH DELETE ch",
            source_id=source_id,
            project_id=project_id,
        )
        self.run_write(
            "MATCH (s:Source {source_id: $source_id, project_id: $project_id})"
            " DETACH DELETE s",
            source_id=source_id,
            project_id=project_id,
        )
        if affected:
            self.run_write(
                "MATCH (k:Concept {project_id: $project_id})"
                " WHERE k.concept_id IN $ids AND NOT (:Claim)-[:IN_CONCEPT]->(k)"
                " DETACH DELETE k",
                ids=affected,
                project_id=project_id,
            )
            self.run_write(
                "MATCH (k:Concept {project_id: $project_id})"
                " WHERE k.concept_id IN $ids"
                " SET k.dirty = true, k.teach_md = ''",
                ids=affected,
                project_id=project_id,
            )
        return affected

    def set_claim_trust(self, claim_id: str, trust: str, *, project_id: str = "default") -> None:
        """No-op if the Claim node doesn't exist — caller must ensure it was upserted first."""
        self.run_write(
            "MATCH (c:Claim {claim_id: $claim_id, project_id: $project_id}) SET c.trust = $trust",
            claim_id=claim_id,
            project_id=project_id,
            trust=trust,
        )

    def _vector_search(
        self,
        index: str,
        embedding: list[float],
        k: int,
        *,
        project_id: str = "default",
        include_embedding: bool = False,
    ) -> list[dict]:
        over_k = max(k * 5, k + 10)
        rows = self.run_read(
            f"CALL db.index.vector.queryNodes('{index}', $k, $embedding) "
            "YIELD node, score RETURN node{.*, score: score} AS hit",
            k=over_k,
            embedding=embedding,
        )
        hits = [r["hit"] for r in rows]
        if not include_embedding:
            for hit in hits:
                hit.pop("embedding", None)
        return [h for h in hits if h.get("project_id", "default") == project_id][:k]

    def vector_search_claims(
        self,
        embedding: list[float],
        k: int = 10,
        *,
        project_id: str = "default",
        include_embedding: bool = False,
    ) -> list[dict]:
        return self._vector_search(
            "claim_embedding",
            embedding,
            k,
            project_id=project_id,
            include_embedding=include_embedding,
        )

    def vector_search_chunks(
        self,
        embedding: list[float],
        k: int = 10,
        *,
        project_id: str = "default",
        include_embedding: bool = False,
    ) -> list[dict]:
        return self._vector_search(
            "chunk_embedding",
            embedding,
            k,
            project_id=project_id,
            include_embedding=include_embedding,
        )

    # -- concepts -----------------------------------------------------------
    def upsert_concept(self, concept, *, project_id: str = "default") -> None:
        self.run_write(
            "MERGE (k:Concept {concept_id: $concept_id, project_id: $project_id}) "
            "SET k.name = $name, k.summary = $summary",
            concept_id=concept.concept_id,
            project_id=project_id,
            name=concept.name,
            summary=concept.summary,
        )

    def assign_claim(self, claim_id: str, concept_id: str, *, project_id: str = "default") -> None:
        """No-op if Claim or Concept doesn't exist — caller must ensure they were upserted first."""
        self.run_write(
            "MATCH (c:Claim {claim_id: $claim_id, project_id: $project_id}), "
            "(k:Concept {concept_id: $concept_id, project_id: $project_id}) "
            "MERGE (c)-[:IN_CONCEPT]->(k)",
            claim_id=claim_id,
            concept_id=concept_id,
            project_id=project_id,
        )

    def add_depends_on(
        self, from_concept_id: str, to_concept_id: str, *, project_id: str = "default"
    ) -> None:
        """No-op if either Concept doesn't exist — caller must ensure they were upserted first."""
        self.run_write(
            "MATCH (a:Concept {concept_id: $a, project_id: $project_id}), "
            "(b:Concept {concept_id: $b, project_id: $project_id}) "
            "MERGE (a)-[:DEPENDS_ON]->(b)",
            a=from_concept_id,
            b=to_concept_id,
            project_id=project_id,
        )

    def add_conflict(
        self,
        claim_a: str,
        claim_b: str,
        classification: str,
        rationale: str,
        *,
        project_id: str = "default",
    ) -> None:
        """Creates/updates normalized CONFLICTS_WITH edge for a claim pair."""
        validate_classification(classification)
        claim_a, claim_b = sorted((claim_a, claim_b))
        self.run_write(
            "MATCH (a:Claim {claim_id: $a, project_id: $project_id}), "
            "(b:Claim {claim_id: $b, project_id: $project_id}) "
            "MERGE (a)-[r:CONFLICTS_WITH]->(b) "
            "SET r.classification = $classification, r.rationale = $rationale",
            a=claim_a,
            b=claim_b,
            project_id=project_id,
            classification=classification,
            rationale=rationale,
        )

    def claims_in_concept(self, concept_id: str, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim {project_id: $project_id})-[:IN_CONCEPT]->"
            "(:Concept {concept_id: $concept_id, project_id: $project_id}) "
            "OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(ch:Chunk {project_id: $project_id}) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.quote AS quote, c.trust AS trust, c.source_id AS source_id, "
            "c.kind AS kind, ch.chunk_id AS chunk_id ORDER BY c.claim_id",
            concept_id=concept_id,
            project_id=project_id,
        )

    def conflicts_in_concept(self, concept_id: str, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (a:Claim {project_id: $project_id})-[r:CONFLICTS_WITH]->"
            "(b:Claim {project_id: $project_id}), "
            "(a)-[:IN_CONCEPT]->(k:Concept {concept_id: $concept_id, project_id: $project_id}), "
            "(b)-[:IN_CONCEPT]->(k) "
            "RETURN a.claim_id AS claim_a, b.claim_id AS claim_b, "
            "a.text AS text_a, b.text AS text_b, "
            "r.classification AS classification, r.rationale AS rationale",
            concept_id=concept_id,
            project_id=project_id,
        )

    def concept_dependencies(self, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (a:Concept {project_id: $project_id})-[:DEPENDS_ON]->"
            "(b:Concept {project_id: $project_id}) "
            "RETURN a.concept_id AS from_id, b.concept_id AS to_id",
            project_id=project_id,
        )

    def mark_concept_dirty(
        self, concept_id: str, dirty: bool = True, *, project_id: str = "default"
    ) -> None:
        """No-op if the Concept node doesn't exist — caller must ensure it was upserted first."""
        self.run_write(
            "MATCH (k:Concept {concept_id: $concept_id, project_id: $project_id}) "
            "SET k.dirty = $dirty",
            concept_id=concept_id,
            project_id=project_id,
            dirty=dirty,
        )

    def dirty_concepts(self, *, project_id: str = "default") -> list[str]:
        return [
            r["concept_id"]
            for r in self.run_read(
                "MATCH (k:Concept {project_id: $project_id, dirty: true}) "
                "RETURN k.concept_id AS concept_id ORDER BY k.concept_id",
                project_id=project_id,
            )
        ]

    def unassigned_trusted_claims(self, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim {project_id: $project_id}) "
            "WHERE c.trust IN ['trusted', 'escalated', 'image_observed'] "
            "AND NOT (c)-[:IN_CONCEPT]->(:Concept {project_id: $project_id}) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.source_id AS source_id, c.embedding AS embedding "
            "ORDER BY c.claim_id",
            project_id=project_id,
        )

    def concept_id_of_claim(self, claim_id: str, *, project_id: str = "default") -> str | None:
        rows = self.run_read(
            "MATCH (:Claim {claim_id: $claim_id, project_id: $project_id})"
            "-[:IN_CONCEPT]->(k:Concept {project_id: $project_id}) "
            "RETURN k.concept_id AS concept_id LIMIT 1",
            claim_id=claim_id,
            project_id=project_id,
        )
        return rows[0]["concept_id"] if rows else None

    def get_concept(self, concept_id: str, *, project_id: str = "default") -> dict | None:
        rows = self.run_read(
            "MATCH (k:Concept {concept_id: $concept_id, project_id: $project_id}) "
            "RETURN k.concept_id AS concept_id, k.name AS name, "
            "k.summary AS summary, k.order_index AS order_index, "
            "coalesce(k.dirty, false) AS dirty, coalesce(k.teach_md, '') AS teach_md, "
            "k.teach_at AS teach_at",
            concept_id=concept_id,
            project_id=project_id,
        )
        return rows[0] if rows else None

    def set_concept_teaching(
        self, concept_id: str, teach_md: str, *, project_id: str = "default"
    ) -> None:
        teach_at = time.time() if teach_md else None
        self.run_write(
            "MATCH (k:Concept {concept_id: $concept_id, project_id: $project_id}) "
            "SET k.teach_md = $teach_md, k.teach_at = $teach_at",
            concept_id=concept_id,
            project_id=project_id,
            teach_md=teach_md,
            teach_at=teach_at,
        )

    def citations_for_claims(
        self, claim_ids: list[str], *, project_id: str = "default"
    ) -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim {project_id: $project_id})-[:EXTRACTED_FROM]->(ch:Chunk {project_id: $project_id}) "
            "WHERE c.claim_id IN $claim_ids "
            "RETURN c.claim_id AS claim_id, ch.chunk_id AS chunk_id, "
            "ch.source_id AS source_id, ch.seq AS seq, ch.unit_index AS unit_index, "
            "ch.kind AS kind, ch.page AS page, ch.href AS href, ch.url AS url, "
            "ch.para_index AS para_index, ch.start_s AS start_s, ch.end_s AS end_s "
            "ORDER BY c.claim_id",
            claim_ids=claim_ids,
            project_id=project_id,
        )

    def set_concept_meta(
        self,
        concept_id: str,
        name: str | None = None,
        summary: str | None = None,
        order_index: int | None = None,
        *,
        project_id: str = "default",
    ) -> None:
        updates: list[str] = []
        params: dict = {"concept_id": concept_id, "project_id": project_id}
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
            "MATCH (k:Concept {concept_id: $concept_id, project_id: $project_id}) "
            f"SET {', '.join(updates)}",
            **params,
        )

    def all_concepts(self, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (k:Concept {project_id: $project_id}) "
            "RETURN k.concept_id AS concept_id, k.name AS name, "
            "k.summary AS summary, k.order_index AS order_index, "
            "coalesce(k.dirty, false) AS dirty "
            "ORDER BY k.concept_id",
            project_id=project_id,
        )

    def spine_concept_order(self, *, project_id: str = "default") -> list[dict]:
        return self.run_read(
            "MATCH (cl:Claim {project_id: $project_id})-[:IN_CONCEPT]->(k:Concept {project_id: $project_id}), "
            "(cl)-[:EXTRACTED_FROM]->(ch:Chunk {project_id: $project_id})"
            "<-[:HAS_CHUNK]-(s:Source {role:'spine', project_id: $project_id}) "
            "RETURN k.concept_id AS concept_id, min(ch.seq) AS first_seq "
            "ORDER BY first_seq",
            project_id=project_id,
        )

    def curriculum(self, *, project_id: str = "default") -> list[dict]:
        # conflict_count rides along so the UI's conflict badges don't need a
        # per-concept detail fetch (one aggregate here vs. an N+1 fan-out).
        return self.run_read(
            "MATCH (k:Concept {project_id: $project_id}) WHERE k.order_index IS NOT NULL "
            "OPTIONAL MATCH (a:Claim {project_id: $project_id})-[c:CONFLICTS_WITH]->"
            "(b:Claim {project_id: $project_id}), "
            "(a)-[:IN_CONCEPT]->(k), (b)-[:IN_CONCEPT]->(k) "
            "RETURN k.concept_id AS concept_id, k.name AS name, "
            "k.summary AS summary, k.order_index AS order_index, "
            "count(c) AS conflict_count "
            "ORDER BY k.order_index",
            project_id=project_id,
        )

    # -- export -----------------------------------------------------------
    def export_all(self, *, project_id: str = "default") -> tuple[list[dict], list[dict]]:
        nodes = [
            {
                "id": f"n{r['nid']}",
                "labels": r["labels"],
                "properties": {k: v for k, v in r["props"].items() if k != "embedding"},
            }
            for r in self.run_read(
                "MATCH (n {project_id: $project_id}) "
                "RETURN id(n) AS nid, labels(n) AS labels, properties(n) AS props",
                project_id=project_id,
            )
        ]
        rels = [
            {
                "start": f"n{r['start']}",
                "end": f"n{r['end']}",
                "type": r["type"],
                "properties": r["props"],
            }
            for r in self.run_read(
                "MATCH (a {project_id: $project_id})-[r]->(b {project_id: $project_id}) "
                "RETURN id(a) AS start, id(b) AS end, type(r) AS type, properties(r) AS props",
                project_id=project_id,
            )
        ]
        return nodes, rels
