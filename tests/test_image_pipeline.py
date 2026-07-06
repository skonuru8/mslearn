"""End-to-end: an image source ingests to image_observed claims offline."""
import pytest

from mslearn.opsdb import OpsDB
from mslearn.pipeline.orchestrator import ingest_source
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import CHUNK, GOOD, ScriptedRouter


@pytest.fixture(autouse=True)
def eager_app():
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = False


def test_image_source_ingests_to_image_observed_claims(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    # Enough scripted responses for extraction AND the eager synthesis pass
    # (clustering/naming) that fires when the source completes.
    ctx = PipelineContext(
        settings=None, db=db, router=ScriptedRouter([GOOD] * 8), graph=graph
    )
    set_context(ctx)

    # The vision model is faked: it "reads" the image and returns text whose
    # content the extraction fixture can quote-match (CHUNK), so the whole
    # chunk -> extract -> trust-gate path runs exactly as for a text source.
    monkeypatch.setattr(
        worker_tasks, "image_describe_via_router",
        lambda router, opsdb: (lambda image_bytes, media_type: CHUNK),
    )

    image = tmp_path / "notes.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfakepixels")

    source_id = ingest_source(str(image), role="spine", source_type="image")

    row = db.source_row(source_id)
    assert row["status"] == "done"
    assert graph.source_type_of(source_id) == "image"
    committed = list(graph.claims.values())
    assert committed, "no claims committed from the image"
    assert all(c["trust"] == "image_observed" for c in committed)
    # The image claim is usable course material: the eager synthesis pass
    # clustered it into a concept (so it will appear in the curriculum /
    # teaching, badged as image-observed).
    assert graph.claim_to_concept, "image claim was not clustered into a concept"
    assert graph.concepts, "no concept formed from the image"
