from mslearn.pipeline.contracts import to_claim_record
from mslearn.pipeline.extraction_graph import run_extraction
from mslearn.providers.base import ProviderTransientError
from mslearn.worker.app import app
from mslearn.worker.context import get_context


def _check_failure_monitor(db, source_id: str) -> None:
    stats = db.failure_stats(source_id)
    min_chunks = int(db.get_tunable("monitor.min_chunks"))
    threshold = db.get_tunable("monitor.failure_rate_threshold")
    if stats["total"] >= min_chunks and stats["failed"] / stats["total"] > threshold:
        db.set_source_status(source_id, "paused",
                             error=f"failure rate {stats['failed']}/{stats['total']}")


@app.task(
    bind=True,
    autoretry_for=(ProviderTransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def extract_chunk_task(self, chunk_id: str):
    ctx = get_context()
    source_id = chunk_id.rsplit(":", 1)[0]

    source = ctx.db.source_row(source_id)
    if source is not None and source["status"] == "paused":
        ctx.db.mark_chunk(chunk_id, "skipped_paused")
        return

    chunk = ctx.graph.get_chunk(chunk_id)
    if chunk is None:
        ctx.db.mark_chunk(chunk_id, "failed", error="chunk not found in graph")
        _check_failure_monitor(ctx.db, source_id)
        return

    state = run_extraction(ctx.router, ctx.db, chunk_id, chunk["text"])
    if state["error"] is not None:
        ctx.db.mark_chunk(chunk_id, "failed", error=state["error"])
        _check_failure_monitor(ctx.db, source_id)
        return

    trust = "escalated" if state["escalated"] else "trusted"
    accepted = state["accepted"]
    if accepted:
        embeddings = ctx.router.embed([d.text for d in accepted])
        for draft, embedding in zip(accepted, embeddings):
            record = to_claim_record(
                draft, chunk_id=chunk_id, source_id=chunk["source_id"], trust=trust
            )
            ctx.graph.upsert_claim(record, embedding)

    error = f"{len(state['rejected'])} claims rejected" if state["rejected"] else None
    ctx.db.mark_chunk(chunk_id, "done", error=error)
