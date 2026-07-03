import json
import time

from mslearn.pipeline.contracts import to_claim_record
from mslearn.pipeline.extraction_graph import run_extraction
from mslearn.pipeline.synthesis import (
    build_curriculum,
    cluster_new_claims,
    process_dirty_concepts,
)
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


def _finalize_chunk(ctx, source_id: str, chunk_id: str, status: str, error: str | None = None) -> None:
    ctx.db.mark_chunk(chunk_id, status, error=error)
    if status == "failed":
        _check_failure_monitor(ctx.db, source_id)
    if ctx.db.try_complete_source(source_id):
        synthesize_task.delay()


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
        _finalize_chunk(ctx, source_id, chunk_id, "failed", "chunk not found in graph")
        return

    try:
        state = run_extraction(ctx.router, ctx.db, chunk_id, chunk["text"])
    except ProviderTransientError as exc:
        if self.request.retries >= self.max_retries:
            _finalize_chunk(
                ctx, source_id, chunk_id, "failed", f"retries exhausted: {exc}"[:500]
            )
        raise
    if state["error"] is not None:
        _finalize_chunk(ctx, source_id, chunk_id, "failed", state["error"])
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

    if not accepted and state["rejected"]:
        reasons = state["rejected"][0].get("reasons", [])
        error = "; ".join(reasons) if reasons else "claims rejected"
        _finalize_chunk(ctx, source_id, chunk_id, "failed", error[:500])
        return

    error = f"{len(state['rejected'])} claims rejected" if state["rejected"] else None
    _finalize_chunk(ctx, source_id, chunk_id, "done", error)


@app.task
def synthesize_task():
    ctx = get_context()
    dirty = cluster_new_claims(ctx)
    processed = process_dirty_concepts(ctx)
    ordered = build_curriculum(ctx)
    payload = {
        "ts": int(time.time()),
        "dirty_concepts": len(dirty),
        "processed_concepts": processed,
        "curriculum_len": len(ordered),
    }
    ctx.db.set_setting("synthesis:last_run", json.dumps(payload, sort_keys=True))
