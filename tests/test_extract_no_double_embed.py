"""The trust gate in validate() already embeds every draft claim's text to
check quote similarity. Storage used to embed the SAME accepted claim texts
again at commit time — one redundant network round trip per chunk. This test
proves each accepted claim's text is embedded exactly once across the whole
extract_chunk_task, end to end."""

import pytest

from mslearn.opsdb import OpsDB
from mslearn.pipeline.contracts import derive_claim_id
from mslearn.pipeline.extraction_graph import build_extraction_graph
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.test_extraction_graph import ScriptedRouter
from tests.test_worker_tasks import FakeGraph

CHUNK = (
    "Cache invalidation is one of the two hard problems in computer science. "
    "Off-by-one errors also cause bugs."
)
TWO_CLAIMS = {"claims": [
    {"text": "Cache invalidation is hard.", "stance": "neutral",
     "quote": "Cache invalidation is one of the two hard problems"},
    {"text": "Off-by-one errors are common.", "stance": "neutral",
     "quote": "Off-by-one errors also cause bugs"},
]}


class LoggingRouter(ScriptedRouter):
    """Records every text string passed to embed() across ALL calls (trust
    gate + any storage call), so we can assert no text is embedded twice."""

    def __init__(self, outputs):
        super().__init__(outputs)
        self.embed_log: list[str] = []

    def embed(self, texts):
        self.embed_log.extend(texts)
        return super().embed(texts)


@pytest.fixture(autouse=True)
def eager_app():
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = False


@pytest.fixture()
def ctx(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    graph = FakeGraph({"s1:0": {"chunk_id": "s1:0", "source_id": "s1", "text": CHUNK}})
    db.register_source("s1", ref="r", role="spine", total_chunks=1)
    db.register_chunk_jobs("s1", ["s1:0"])

    def make(router):
        context = PipelineContext(
            settings=None, db=db, router=router, graph=graph,
            extraction_graph=build_extraction_graph(router, db),
        )
        set_context(context)
        return context

    return make


def test_accepted_claims_each_embedded_exactly_once(ctx):
    router = LoggingRouter([TWO_CLAIMS])
    context = ctx(router)

    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()

    claim_texts = [c["text"] for c in TWO_CLAIMS["claims"]]

    # No redundant re-embed at storage time: each accepted claim's text shows
    # up in the embed log exactly once across the whole task, whether it came
    # from the trust gate's batched call or (if ever) a storage fallback.
    for text in claim_texts:
        assert router.embed_log.count(text) == 1, (
            f"{text!r} embedded {router.embed_log.count(text)} times, expected 1"
        )

    # Sanity: both claims actually landed in storage with real embeddings.
    assert len(context.graph.claims) == 2
    for text in claim_texts:
        cid = derive_claim_id("s1:0", text)
        claim, embedding = context.graph.claims[cid]
        assert claim.trust == "trusted"
        assert embedding and len(embedding) > 0
