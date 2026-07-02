# Plan 3/8: Knowledge Graph Layer (Neo4j) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Neo4j-backed concept graph: schema/constraints, vector indexes, upserts for sources/chunks/claims, concept clustering edges (`DEPENDS_ON`, `CONFLICTS_WITH{classification, rationale}`), vector search, and portable GraphML/JSON export.

**Architecture:** `mslearn/graph/` package — `records.py` (graph-side dataclasses + conflict taxonomy), `store.py` (`GraphStore` wrapping the official neo4j driver; all Cypher lives here), `export.py` (pure-Python GraphML/JSON serializers — no APOC dependency, so export is unit-testable offline and works on any Neo4j). Integration tests talk to the real Neo4j from docker-compose and **skip cleanly when it isn't running**; serializer/record tests are pure-offline.

**Tech Stack (added):** `neo4j>=5.20` Python driver. Embeddings: 768-dim (nomic-embed-text), dimension configurable.

## Global Constraints

- All Cypher lives in `mslearn/graph/store.py` — no other module builds query strings
- Node labels exactly: `Source`, `Chunk`, `Claim`, `Concept`; relationships exactly: `HAS_CHUNK` (Source→Chunk), `EXTRACTED_FROM` (Claim→Chunk), `IN_CONCEPT` (Claim→Concept), `DEPENDS_ON` (Concept→Concept), `CONFLICTS_WITH` (Claim→Claim, properties `classification` + `rationale`)
- Conflict classifications exactly: `context_dependent`, `outdated`, `genuine_debate`, `evidence_mismatch` (validated, ValueError otherwise)
- Upserts are idempotent (MERGE on unique keys) — re-running ingestion never duplicates nodes
- Offline suite stays green with Neo4j down: integration tests use the `graph_store` fixture which `pytest.skip`s when bolt is unreachable; marker `neo4j` registered
- Export never uses APOC — pure-Python serializers fed by `GraphStore.export_all()`
- Prior suite (85 tests) stays green; ruff clean; commits per task

---

### Task 1: neo4j dep + graph records

**Files:**
- Modify: `pyproject.toml` (add `"neo4j>=5.20"` to dependencies)
- Create: `mslearn/graph/__init__.py` (empty), `mslearn/graph/records.py`, `tests/test_graph_records.py`

**Interfaces:**
- Produces: `CONFLICT_CLASSIFICATIONS = ("context_dependent", "outdated", "genuine_debate", "evidence_mismatch")`; `ClaimRecord{claim_id, chunk_id, source_id, text, stance, quote, trust}` (frozen); `ConceptRecord{concept_id, name, summary=""}` (frozen); `validate_classification(value) -> str` (returns value or raises ValueError). Plans 4–5 import these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_records.py
import pytest

from mslearn.graph.records import (
    CONFLICT_CLASSIFICATIONS,
    ClaimRecord,
    ConceptRecord,
    validate_classification,
)


def test_conflict_taxonomy_exact():
    assert CONFLICT_CLASSIFICATIONS == (
        "context_dependent", "outdated", "genuine_debate", "evidence_mismatch",
    )


def test_validate_classification():
    assert validate_classification("outdated") == "outdated"
    with pytest.raises(ValueError, match="unknown conflict classification"):
        validate_classification("vibes")


def test_records_frozen():
    claim = ClaimRecord(claim_id="c1", chunk_id="ch1", source_id="s1",
                        text="t", stance="neutral", quote="q", trust="trusted")
    with pytest.raises(AttributeError):
        claim.text = "other"
    concept = ConceptRecord(concept_id="k1", name="Caching")
    assert concept.summary == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_graph_records.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.graph'`

- [ ] **Step 3: Implement + add dependency**

Add `"neo4j>=5.20",` to `pyproject.toml` dependencies. `mslearn/graph/__init__.py` is empty.

```python
# mslearn/graph/records.py
from dataclasses import dataclass

CONFLICT_CLASSIFICATIONS = (
    "context_dependent",
    "outdated",
    "genuine_debate",
    "evidence_mismatch",
)


def validate_classification(value: str) -> str:
    if value not in CONFLICT_CLASSIFICATIONS:
        raise ValueError(f"unknown conflict classification {value!r}")
    return value


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: str
    chunk_id: str
    source_id: str
    text: str
    stance: str  # "recommends" | "warns_against" | "neutral"
    quote: str
    trust: str   # "trusted" | "escalated" | "rejected"


@dataclass(frozen=True)
class ConceptRecord:
    concept_id: str
    name: str
    summary: str = ""
```

- [ ] **Step 4: Install + run**

Run: `.venv/bin/pip install -e ".[dev]" -q && .venv/bin/pytest tests/test_graph_records.py -v && .venv/bin/ruff check .`
Expected: 3 PASSED; ruff clean

- [ ] **Step 5: Full suite, commit**

Run: `.venv/bin/pytest -q` → 88 passed.

```bash
git add pyproject.toml mslearn/graph/ tests/test_graph_records.py
git commit -m "feat: graph records, conflict taxonomy, neo4j driver dep"
```

---

### Task 2: GraphStore connection, schema, wipe + integration fixture

**Files:**
- Create: `mslearn/graph/store.py`, `tests/test_graph_store.py`
- Modify: `pyproject.toml` (register `neo4j` pytest marker), `tests/conftest.py` (append fixture)

**Interfaces:**
- Produces: `GraphStore(uri, user, password, embedding_dim=768)` with `ping()`, `ensure_schema()` (4 unique constraints + 2 vector indexes `claim_embedding`/`chunk_embedding`, cosine), `wipe()` (test-only full delete), `close()`; context-manager support. `graph_store` session fixture (skips when Neo4j down, ensures schema, wipes before yield). Every later graph task uses both.

- [ ] **Step 1: Register marker + fixture + failing test**

In `pyproject.toml` `[tool.pytest.ini_options]` markers list, add: `"neo4j: integration tests requiring a running Neo4j"`.

Append to `tests/conftest.py`:

```python
@pytest.fixture(scope="session")
def graph_store():
    from mslearn.graph.store import GraphStore
    from mslearn.settings import Settings

    settings = Settings(_env_file=None)
    try:
        store = GraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        store.ping()
    except Exception:
        pytest.skip("neo4j not reachable — start it with `make services`")
    store.ensure_schema()
    yield store
    store.close()


@pytest.fixture()
def clean_graph(graph_store):
    graph_store.wipe()
    return graph_store
```

```python
# tests/test_graph_store.py
import pytest

pytestmark = pytest.mark.neo4j


def test_ping_and_schema(clean_graph):
    indexes = clean_graph.list_index_names()
    assert "claim_embedding" in indexes and "chunk_embedding" in indexes


def test_wipe_empties_graph(clean_graph):
    clean_graph.run_write("CREATE (:Source {source_id: 'tmp'})")
    assert clean_graph.node_count() == 1
    clean_graph.wipe()
    assert clean_graph.node_count() == 0
```

- [ ] **Step 2: Run to verify failure/skip**

Run: `.venv/bin/pytest tests/test_graph_store.py -v`
Expected: FAIL with ModuleNotFoundError (import happens inside fixture — so expected: errors/skip; after implementing, with Neo4j down these SKIP; with Neo4j up they must PASS)

- [ ] **Step 3: Implement**

```python
# mslearn/graph/store.py
from neo4j import GraphDatabase

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
    def run_write(self, query: str, **params) -> None:
        with self._driver.session() as session:
            session.execute_write(lambda tx: tx.run(query, **params).consume())

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
```

- [ ] **Step 4: Run with service, then without**

Run: `docker compose up -d neo4j && sleep 25 && .venv/bin/pytest tests/test_graph_store.py -v`
Expected: 2 PASSED. Then `.venv/bin/pytest -q` (full suite) → all green, and confirm suite ALSO passes green-with-skips when Neo4j is stopped (note in report which mode ran; if Docker is unavailable in the environment, the skip path is the verified one — say so).

- [ ] **Step 5: Commit**

```bash
git add mslearn/graph/store.py tests/test_graph_store.py tests/conftest.py pyproject.toml
git commit -m "feat: GraphStore with schema constraints, vector indexes, skip-guarded integration fixture"
```

---

### Task 3: Source + chunk upserts

**Files:**
- Modify: `mslearn/graph/store.py`
- Create: `tests/test_graph_ingest.py`

**Interfaces:**
- Consumes: `SourceDocument`/`Chunk` (Plan 2), `GraphStore` low-level helpers.
- Produces: `upsert_source(doc: SourceDocument) -> None` (MERGE Source node: source_id, source_type, role, title); `upsert_chunks(chunks: list[Chunk], embeddings: list[list[float]]) -> None` (MERGE Chunk nodes with text, seq, unit_index, flattened locator props kind/page/href/url/para_index/start_s/end_s, embedding; `HAS_CHUNK` edge from source); `chunks_for_source(source_id) -> list[dict]` ordered by seq. Idempotent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_ingest.py
import pytest

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit
from mslearn.chunking import chunk_source

pytestmark = pytest.mark.neo4j


def make_doc():
    return SourceDocument(
        source_id="srcA", source_type="pdf", role="spine", title="Book A",
        units=[StructuralUnit(0, "p1", "Caching is hard.\n\nInvalidation is harder.",
                              Locator(kind="page", page=1))],
    )


def embed_stub(chunks):
    return [[float(i)] * 768 for i, _ in enumerate(chunks)]


def test_upsert_source_and_chunks_idempotent(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    clean_graph.upsert_source(doc)  # re-run: no duplicates
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))

    rows = clean_graph.chunks_for_source("srcA")
    assert len(rows) == len(chunks)
    assert rows[0]["seq"] == 0
    assert rows[0]["kind"] == "page" and rows[0]["page"] == 1
    assert clean_graph.node_count() == 1 + len(chunks)


def test_chunk_embedding_length_mismatch_raises(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    with pytest.raises(ValueError, match="embeddings length"):
        clean_graph.upsert_chunks(chunks, [])
```

- [ ] **Step 2: Run to verify failure** (with Neo4j up)

Run: `.venv/bin/pytest tests/test_graph_ingest.py -v`
Expected: FAIL with `AttributeError: 'GraphStore' object has no attribute 'upsert_source'`

- [ ] **Step 3: Implement** (append methods to `GraphStore`)

```python
    # -- ingest -----------------------------------------------------------
    def upsert_source(self, doc) -> None:
        self.run_write(
            "MERGE (s:Source {source_id: $source_id}) "
            "SET s.source_type = $source_type, s.role = $role, s.title = $title",
            source_id=doc.source_id, source_type=doc.source_type,
            role=doc.role, title=doc.title,
        )

    def upsert_chunks(self, chunks, embeddings) -> None:
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
```

- [ ] **Step 4: Run tests** (Neo4j up)

Run: `.venv/bin/pytest tests/test_graph_ingest.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Full suite + commit**

```bash
git add mslearn/graph/store.py tests/test_graph_ingest.py
git commit -m "feat: idempotent source/chunk upserts with flattened locators and embeddings"
```

---

### Task 4: Claim upserts + vector search

**Files:**
- Modify: `mslearn/graph/store.py`
- Create: `tests/test_graph_claims.py`

**Interfaces:**
- Consumes: `ClaimRecord` (Task 1).
- Produces: `upsert_claim(claim: ClaimRecord, embedding: list[float]) -> None` (MERGE Claim; props text/stance/quote/trust/source_id/embedding; `EXTRACTED_FROM` → Chunk); `claims_for_source(source_id) -> list[dict]`; `set_claim_trust(claim_id, trust)`; `vector_search_claims(embedding, k=10) -> list[dict]` (each dict includes `score`); `vector_search_chunks(embedding, k=10)` same shape. Plans 4–6 use these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_claims.py
import pytest

from mslearn.graph.records import ClaimRecord
from tests.test_graph_ingest import embed_stub, make_doc
from mslearn.chunking import chunk_source

pytestmark = pytest.mark.neo4j


def seeded(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    return clean_graph, chunks


def claim(cid, chunk_id, text="Cache invalidation is hard."):
    return ClaimRecord(claim_id=cid, chunk_id=chunk_id, source_id="srcA",
                       text=text, stance="neutral", quote="Invalidation is harder.",
                       trust="trusted")


def unit_vec(idx, dim=768):
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def test_upsert_claim_idempotent_and_linked(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))  # rerun
    rows = store.claims_for_source("srcA")
    assert len(rows) == 1
    assert rows[0]["claim_id"] == "cl1" and rows[0]["chunk_id"] == chunks[0].chunk_id
    assert rows[0]["trust"] == "trusted"


def test_set_claim_trust(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))
    store.set_claim_trust("cl1", "rejected")
    assert store.claims_for_source("srcA")[0]["trust"] == "rejected"


def test_vector_search_orders_by_similarity(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id, "about caching"), unit_vec(0))
    store.upsert_claim(claim("cl2", chunks[0].chunk_id, "about testing"), unit_vec(1))
    hits = store.vector_search_claims(unit_vec(0), k=2)
    assert hits[0]["claim_id"] == "cl1"
    assert hits[0]["score"] >= hits[1]["score"]
```

- [ ] **Step 2: Run to verify failure** (Neo4j up)

Expected: `AttributeError: ... no attribute 'upsert_claim'`

- [ ] **Step 3: Implement** (append to `GraphStore`)

```python
    # -- claims -----------------------------------------------------------
    def upsert_claim(self, claim, embedding: list[float]) -> None:
        self.run_write(
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
        self.run_write(
            "MATCH (c:Claim {claim_id: $claim_id}) SET c.trust = $trust",
            claim_id=claim_id, trust=trust,
        )

    def _vector_search(self, index: str, embedding: list[float], k: int) -> list[dict]:
        return self.run_read(
            f"CALL db.index.vector.queryNodes('{index}', $k, $embedding) "
            "YIELD node, score RETURN node{.*, score: score} AS hit",
            k=k, embedding=embedding,
        )

    def vector_search_claims(self, embedding: list[float], k: int = 10) -> list[dict]:
        return [r["hit"] for r in self._vector_search("claim_embedding", embedding, k)]

    def vector_search_chunks(self, embedding: list[float], k: int = 10) -> list[dict]:
        return [r["hit"] for r in self._vector_search("chunk_embedding", embedding, k)]
```

Note: hits include the raw `embedding` property; that's acceptable at this layer.

- [ ] **Step 4: Run tests** (Neo4j up) — 3 PASSED

- [ ] **Step 5: Full suite + commit**

```bash
git add mslearn/graph/store.py tests/test_graph_claims.py
git commit -m "feat: claim upserts, trust updates, vector search over claims/chunks"
```

---

### Task 5: Concepts, DEPENDS_ON, CONFLICTS_WITH + queries

**Files:**
- Modify: `mslearn/graph/store.py`
- Create: `tests/test_graph_concepts.py`

**Interfaces:**
- Consumes: `ConceptRecord`, `validate_classification` (Task 1).
- Produces: `upsert_concept(concept: ConceptRecord)`; `assign_claim(claim_id, concept_id)` (`IN_CONCEPT`); `add_depends_on(from_concept_id, to_concept_id)`; `add_conflict(claim_a, claim_b, classification, rationale)` (validates classification; single edge per pair via MERGE); `claims_in_concept(concept_id) -> list[dict]`; `conflicts_in_concept(concept_id) -> list[dict]` (pairs where both claims are IN_CONCEPT, returns claim ids + classification + rationale); `concept_dependencies() -> list[dict]` (all DEPENDS_ON edges); `dirty-marking helpers`: `mark_concept_dirty(concept_id, dirty=True)` + `dirty_concepts() -> list[str]`. Plan 5 synthesis builds on all of these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_concepts.py
import pytest

from mslearn.graph.records import ClaimRecord, ConceptRecord
from tests.test_graph_claims import claim, unit_vec
from tests.test_graph_ingest import embed_stub, make_doc
from mslearn.chunking import chunk_source

pytestmark = pytest.mark.neo4j


def seeded_with_claims(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    clean_graph.upsert_claim(claim("cl1", chunks[0].chunk_id, "caching helps"), unit_vec(0))
    clean_graph.upsert_claim(claim("cl2", chunks[0].chunk_id, "caching hurts"), unit_vec(1))
    return clean_graph


def test_concept_assignment_and_queries(clean_graph):
    store = seeded_with_claims(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.assign_claim("cl1", "k1")
    store.assign_claim("cl2", "k1")
    rows = store.claims_in_concept("k1")
    assert {r["claim_id"] for r in rows} == {"cl1", "cl2"}


def test_conflict_edge_validated_and_queryable(clean_graph):
    store = seeded_with_claims(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.assign_claim("cl1", "k1")
    store.assign_claim("cl2", "k1")
    with pytest.raises(ValueError):
        store.add_conflict("cl1", "cl2", "vibes", "nope")
    store.add_conflict("cl1", "cl2", "genuine_debate", "sources disagree on tradeoff")
    store.add_conflict("cl1", "cl2", "genuine_debate", "sources disagree on tradeoff")
    conflicts = store.conflicts_in_concept("k1")
    assert len(conflicts) == 1
    assert conflicts[0]["classification"] == "genuine_debate"


def test_depends_on_and_dirty(clean_graph):
    store = seeded_with_claims(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.upsert_concept(ConceptRecord(concept_id="k2", name="Invalidation"))
    store.add_depends_on("k2", "k1")
    deps = store.concept_dependencies()
    assert {"from_id": "k2", "to_id": "k1"} in [
        {"from_id": d["from_id"], "to_id": d["to_id"]} for d in deps
    ]
    store.mark_concept_dirty("k1")
    assert store.dirty_concepts() == ["k1"]
    store.mark_concept_dirty("k1", dirty=False)
    assert store.dirty_concepts() == []
```

- [ ] **Step 2: Run to verify failure** (Neo4j up) — AttributeError expected

- [ ] **Step 3: Implement** (append to `GraphStore`; import `validate_classification` at top of store.py)

```python
    # -- concepts -----------------------------------------------------------
    def upsert_concept(self, concept) -> None:
        self.run_write(
            "MERGE (k:Concept {concept_id: $concept_id}) "
            "SET k.name = $name, k.summary = $summary",
            concept_id=concept.concept_id, name=concept.name, summary=concept.summary,
        )

    def assign_claim(self, claim_id: str, concept_id: str) -> None:
        self.run_write(
            "MATCH (c:Claim {claim_id: $claim_id}), (k:Concept {concept_id: $concept_id}) "
            "MERGE (c)-[:IN_CONCEPT]->(k)",
            claim_id=claim_id, concept_id=concept_id,
        )

    def add_depends_on(self, from_concept_id: str, to_concept_id: str) -> None:
        self.run_write(
            "MATCH (a:Concept {concept_id: $a}), (b:Concept {concept_id: $b}) "
            "MERGE (a)-[:DEPENDS_ON]->(b)",
            a=from_concept_id, b=to_concept_id,
        )

    def add_conflict(self, claim_a: str, claim_b: str,
                     classification: str, rationale: str) -> None:
        validate_classification(classification)
        self.run_write(
            "MATCH (a:Claim {claim_id: $a}), (b:Claim {claim_id: $b}) "
            "MERGE (a)-[r:CONFLICTS_WITH]->(b) "
            "SET r.classification = $classification, r.rationale = $rationale",
            a=claim_a, b=claim_b, classification=classification, rationale=rationale,
        )

    def claims_in_concept(self, concept_id: str) -> list[dict]:
        return self.run_read(
            "MATCH (c:Claim)-[:IN_CONCEPT]->(:Concept {concept_id: $concept_id}) "
            "RETURN c.claim_id AS claim_id, c.text AS text, c.stance AS stance, "
            "c.trust AS trust, c.source_id AS source_id ORDER BY c.claim_id",
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
        self.run_write(
            "MATCH (k:Concept {concept_id: $concept_id}) SET k.dirty = $dirty",
            concept_id=concept_id, dirty=dirty,
        )

    def dirty_concepts(self) -> list[str]:
        return [r["concept_id"] for r in self.run_read(
            "MATCH (k:Concept {dirty: true}) RETURN k.concept_id AS concept_id "
            "ORDER BY k.concept_id",
        )]
```

- [ ] **Step 4: Run tests** (Neo4j up) — 3 PASSED

- [ ] **Step 5: Full suite + commit**

```bash
git add mslearn/graph/store.py tests/test_graph_concepts.py
git commit -m "feat: concepts, dependency/conflict edges with validated taxonomy, dirty marking"
```

---

### Task 6: Portable export (GraphML + JSON) + README/Makefile

**Files:**
- Create: `mslearn/graph/export.py`, `tests/test_graph_export.py`
- Modify: `mslearn/graph/store.py` (add `export_all`), `Makefile` (add `graph-test`), `README.md` (append Graph section)

**Interfaces:**
- Produces: `GraphStore.export_all() -> tuple[list[dict], list[dict]]` — nodes as `{"id", "labels", "properties"}` (embedding property EXCLUDED from export), relationships as `{"start", "end", "type", "properties"}`; `export.write_json(nodes, rels, path)`; `export.write_graphml(nodes, rels, path)` (valid XML via `xml.etree.ElementTree`, node labels as `labels` attribute key, properties stringified). Serializers are pure functions — offline tests. Plan 6's export pipeline calls all three.

- [ ] **Step 1: Write the failing offline serializer test**

```python
# tests/test_graph_export.py
import json
import xml.etree.ElementTree as ET

from mslearn.graph.export import write_graphml, write_json

NODES = [
    {"id": "n0", "labels": ["Source"], "properties": {"source_id": "srcA", "title": "Book"}},
    {"id": "n1", "labels": ["Claim"], "properties": {"claim_id": "cl1", "text": "x"}},
]
RELS = [
    {"start": "n1", "end": "n0", "type": "EXTRACTED_FROM", "properties": {}},
    {"start": "n1", "end": "n1", "type": "CONFLICTS_WITH",
     "properties": {"classification": "outdated", "rationale": "r"}},
]


def test_write_json_roundtrips(tmp_path):
    path = tmp_path / "graph.json"
    write_json(NODES, RELS, path)
    data = json.loads(path.read_text())
    assert data["nodes"] == NODES and data["relationships"] == RELS


def test_write_graphml_valid_xml(tmp_path):
    path = tmp_path / "graph.graphml"
    write_graphml(NODES, RELS, path)
    root = ET.parse(path).getroot()
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    graph = root.find("g:graph", ns)
    assert len(graph.findall("g:node", ns)) == 2
    edges = graph.findall("g:edge", ns)
    assert len(edges) == 2
    assert {e.get("source") for e in edges} == {"n1"}
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError expected

- [ ] **Step 3: Implement serializers + export_all**

```python
# mslearn/graph/export.py
import json
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "http://graphml.graphdrawing.org/xmlns"


def write_json(nodes: list[dict], rels: list[dict], path: Path | str) -> None:
    Path(path).write_text(
        json.dumps({"nodes": nodes, "relationships": rels}, indent=2, ensure_ascii=False)
    )


def write_graphml(nodes: list[dict], rels: list[dict], path: Path | str) -> None:
    ET.register_namespace("", _NS)
    root = ET.Element(f"{{{_NS}}}graphml")
    graph = ET.SubElement(root, f"{{{_NS}}}graph", edgedefault="directed")
    for node in nodes:
        el = ET.SubElement(graph, f"{{{_NS}}}node", id=node["id"])
        el.set("labels", ";".join(node["labels"]))
        for key, value in node["properties"].items():
            data = ET.SubElement(el, f"{{{_NS}}}data", key=key)
            data.text = "" if value is None else str(value)
    for i, rel in enumerate(rels):
        el = ET.SubElement(
            graph, f"{{{_NS}}}edge",
            id=f"e{i}", source=rel["start"], target=rel["end"],
        )
        el.set("label", rel["type"])
        for key, value in rel["properties"].items():
            data = ET.SubElement(el, f"{{{_NS}}}data", key=key)
            data.text = "" if value is None else str(value)
    ET.ElementTree(root).write(path, xml_declaration=True, encoding="unicode")
```

Append to `GraphStore`:

```python
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
```

Add integration test to `tests/test_graph_export.py`:

```python
import pytest


@pytest.mark.neo4j
def test_export_all_excludes_embeddings(clean_graph):
    from mslearn.chunking import chunk_source
    from tests.test_graph_ingest import embed_stub, make_doc

    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    nodes, rels = clean_graph.export_all()
    assert len(nodes) == 1 + len(chunks)
    assert all("embedding" not in n["properties"] for n in nodes)
    assert any(r["type"] == "HAS_CHUNK" for r in rels)
```

Makefile: add target

```makefile
graph-test:
	docker compose up -d neo4j && sleep 20 && .venv/bin/pytest -m neo4j -v
```

README: append

```markdown
## Concept graph

Neo4j holds the knowledge graph (browser: http://localhost:7474). `GraphStore`
owns all Cypher: schema/vector indexes, source/chunk/claim upserts, concepts
with `DEPENDS_ON` and classified `CONFLICTS_WITH` edges, vector search, and
portable GraphML/JSON export (embeddings excluded). Integration tests need
`make services`; they skip cleanly when Neo4j is down (`make graph-test`).
```

- [ ] **Step 4: Run offline tests, then integration** (Neo4j up)

Run: `.venv/bin/pytest tests/test_graph_export.py -v` → all green (integration part skips if down)

- [ ] **Step 5: Full suite + lint + commit**

Run: `.venv/bin/ruff check . && .venv/bin/pytest -q`

```bash
git add mslearn/graph/export.py mslearn/graph/store.py tests/test_graph_export.py Makefile README.md
git commit -m "feat: portable GraphML/JSON export without APOC, graph-test target"
```

---

## Self-Review (performed at write time)

- **Spec coverage (design §3 graph layer):** labels/relationships exactly as spec'd ✓; conflict taxonomy validated ✓; vector indexes for claims+chunks ✓; dirty-marking for incremental synthesis ✓ (Plan 5 consumes); portability dump ✓ (no APOC, embeddings excluded); Neo4j Browser noted ✓.
- **Placeholder scan:** none.
- **Type consistency:** `ClaimRecord`/`ConceptRecord` fields match store method params; test helpers (`make_doc`, `embed_stub`, `claim`, `unit_vec`) imported across test files consistently; `graph_store`/`clean_graph` fixtures used by Tasks 3–6.
- **Known choices:** integration tests require live Neo4j and skip otherwise (recorded in Global Constraints); `export_all` uses internal `id(n)` for export-file node ids only (never persisted); vector-search hits include stored properties minus nothing (embedding included at this layer, excluded only in exports); executor must note which mode (live vs skip) actually ran in reports.
```
