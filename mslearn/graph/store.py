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
