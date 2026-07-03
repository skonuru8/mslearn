import pytest

from mslearn.opsdb import OpsDB
from mslearn.pipeline.orchestrator import ingest_source, order_corpus, resume_pending
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.test_extraction_graph import ScriptedRouter


class RecordingGraph:
    def __init__(self):
        self.sources = []
        self.chunks = []

    def upsert_source(self, doc, *, project_id="default"):
        self.sources.append(doc.source_id)

    def upsert_chunks(self, chunks, embeddings, *, project_id="default"):
        assert len(chunks) == len(embeddings)
        self.chunks.extend(c.chunk_id for c in chunks)


class NoDelayTask:
    def __init__(self):
        self.delayed = []

    def delay(self, project_id, chunk_id):
        self.delayed.append((project_id, chunk_id))


class NoDelayChunkTask:
    def __init__(self):
        self.delayed = []

    def delay(self, *args):
        self.delayed.append(args)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # ingest_source now hands loading off to chunk_source_task (a Celery
    # task); eager mode makes it — and any extract_chunk_task it in turn
    # schedules — run inline, matching the fully-synchronous flow these
    # tests exercise.
    app.conf.task_always_eager = True
    db = OpsDB(tmp_path / "ops.db")
    graph = RecordingGraph()
    router = ScriptedRouter([])
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))
    fake_task = NoDelayTask()
    monkeypatch.setattr("mslearn.worker.tasks.extract_chunk_task", fake_task)
    monkeypatch.setattr("mslearn.pipeline.orchestrator.extract_chunk_task", fake_task)
    yield db, graph, fake_task
    app.conf.task_always_eager = False


def test_ingest_source_registers_and_enqueues(env, tiny_pdf):
    db, graph, fake_task = env
    source_id = ingest_source(str(tiny_pdf), role="spine")
    assert graph.sources == [source_id]
    row = db.source_row(source_id)
    assert row["status"] == "running" and row["role"] == "spine"
    assert row["total_chunks"] == len(graph.chunks) == len(fake_task.delayed)
    assert all(pid == "default" for pid, _ in fake_task.delayed)


def test_ingest_source_returns_immediately_with_chunking_status(tmp_path, monkeypatch, tiny_pdf):
    # Without eager mode, ingest_source only does the synchronous
    # registration — chunk_source_task is merely scheduled, not run, so the
    # create-source HTTP request returns before any loading happens.
    db = OpsDB(tmp_path / "ops.db")
    graph = RecordingGraph()
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))
    fake_chunk_task = NoDelayChunkTask()
    monkeypatch.setattr("mslearn.pipeline.orchestrator.chunk_source_task", fake_chunk_task)

    source_id = ingest_source(str(tiny_pdf), role="spine")

    row = db.source_row(source_id)
    assert row["status"] == "chunking"
    assert row["total_chunks"] == 0
    assert row["role"] == "spine"
    assert graph.sources == []  # nothing loaded yet — that's chunk_source_task's job
    assert fake_chunk_task.delayed == [("default", source_id, str(tiny_pdf), "spine", None, True)]


def test_ingest_failure_marks_failed(env, tmp_path):
    db, _, _ = env
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    # Adapter failures now surface asynchronously via chunk_source_task (run
    # inline here by the eager fixture) instead of raising synchronously.
    source_id = ingest_source(str(bad))
    sources = db.all_sources()
    assert len(sources) == 1 and sources[0]["source_id"] == source_id
    assert sources[0]["status"] == "failed"


def test_order_corpus_spine_first_then_size(tmp_path):
    small = tmp_path / "small.pdf"
    small.write_bytes(b"x" * 10)
    big = tmp_path / "big.pdf"
    big.write_bytes(b"x" * 1000)
    refs = [(str(big), "supplement"), ("https://a.example/post", "supplement"),
            (str(small), "supplement"), (str(big), "spine")]
    ordered = order_corpus(refs)
    assert ordered[0] == (str(big), "spine")
    assert ordered[1] == (str(small), "supplement")
    assert ordered[2] == (str(big), "supplement")
    assert ordered[3][0].startswith("https://")


def test_resume_pending(env, tiny_pdf):
    db, _, fake_task = env
    ingest_source(str(tiny_pdf))
    fake_task.delayed.clear()
    count = resume_pending()
    assert count == len(db.pending_chunks(db.all_sources()[0]["source_id"]))
    assert len(fake_task.delayed) == count
