import os

import pytest


@pytest.fixture(scope="session")
def tiny_pdf(tmp_path_factory):
    import fitz  # PyMuPDF

    path = tmp_path_factory.mktemp("fixtures") / "tiny.pdf"
    doc = fitz.open()
    for text in [
        "Chapter one. Global mutable state is risky in concurrent code.",
        "Chapter two. Pure functions compose and are easy to test.",
    ]:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="session")
def tiny_epub(tmp_path_factory):
    from ebooklib import epub

    path = tmp_path_factory.mktemp("fixtures") / "tiny.epub"
    book = epub.EpubBook()
    book.set_identifier("tiny-epub-1")
    book.set_title("Tiny Book")
    book.set_language("en")
    ch1 = epub.EpubHtml(title="Ch 1", file_name="ch1.xhtml", lang="en")
    ch1.content = "<html><body><h1>Ch 1</h1><p>Immutability avoids shared-state bugs.</p></body></html>"
    ch2 = epub.EpubHtml(title="Ch 2", file_name="ch2.xhtml", lang="en")
    ch2.content = "<html><body><h1>Ch 2</h1><p>Composition beats inheritance for reuse.</p></body></html>"
    book.add_item(ch1)
    book.add_item(ch2)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch1, ch2]
    epub.write_epub(str(path), book)
    return path


@pytest.fixture(scope="session")
def graph_store():
    from mslearn.graph.store import GraphStore

    # These tests WIPE the database they connect to (clean_graph runs
    # `MATCH (n) DETACH DELETE n`), so they must never touch the production
    # instance. They run only against an explicitly designated test target.
    uri = os.environ.get("MSL_TEST_NEO4J_URI")
    if not uri:
        pytest.skip(
            "graph integration tests are destructive; run them via `make graph-test`"
            " (or set MSL_TEST_NEO4J_URI to a disposable Neo4j instance)"
        )
    user = os.environ.get("MSL_TEST_NEO4J_USER", "neo4j")
    password = os.environ.get("MSL_TEST_NEO4J_PASSWORD", "learnsys-test")
    try:
        store = GraphStore(uri, user, password)
        store.ping()
    except Exception:
        pytest.skip(f"test neo4j not reachable at {uri} — `make graph-test` starts one")
    store.ensure_schema()
    yield store
    store.close()


@pytest.fixture()
def clean_graph(graph_store):
    graph_store.wipe()
    return graph_store
