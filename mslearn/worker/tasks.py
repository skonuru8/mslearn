import json
import logging
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

logger = logging.getLogger(__name__)


def _check_failure_monitor(db, source_id: str) -> None:
    stats = db.failure_stats(source_id)
    min_chunks = int(db.get_tunable("monitor.min_chunks"))
    threshold = db.get_tunable("monitor.failure_rate_threshold")
    if stats["total"] >= min_chunks and stats["problems"] / stats["total"] > threshold:
        db.set_source_status(
            source_id,
            "paused",
            error=f"failure rate {stats['problems']}/{stats['total']}"
            f" ({stats['failed']} failed, {stats['rejected']} rejected)",
        )


def _finalize_chunk(
    ctx, project_id: str, source_id: str, chunk_id: str, status: str, error: str | None = None
) -> None:
    ctx.db.mark_chunk(chunk_id, status, error=error)
    if status in ("failed", "rejected"):
        _check_failure_monitor(ctx.db, source_id)
    if ctx.db.try_complete_source(source_id):
        synthesize_task.delay(project_id)


@app.task(
    bind=True,
    autoretry_for=(ProviderTransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def extract_chunk_task(self, project_id: str, chunk_id: str):
    ctx = get_context()
    source_id = chunk_id.rsplit(":", 1)[0]

    source = ctx.db.source_row(source_id, project_id=project_id)
    if source is not None and source["status"] == "paused":
        ctx.db.mark_chunk(chunk_id, "skipped_paused")
        return

    chunk = ctx.graph.get_chunk(chunk_id, project_id=project_id)
    if chunk is None:
        _finalize_chunk(ctx, project_id, source_id, chunk_id, "failed", "chunk not found in graph")
        return

    try:
        state = run_extraction(ctx.router, ctx.db, chunk_id, chunk["text"])
    except ProviderTransientError as exc:
        if self.request.retries >= self.max_retries:
            _finalize_chunk(
                ctx,
                project_id,
                source_id,
                chunk_id,
                "failed",
                f"retries exhausted: {exc}"[:500],
            )
        raise
    except Exception as exc:
        # Unexpected errors must land in the failures endpoint, not just a
        # worker traceback that dev_up.sh no longer prints at -l warning.
        logger.exception("chunk %s failed unexpectedly", chunk_id)
        _finalize_chunk(
            ctx, project_id, source_id, chunk_id, "failed", f"unexpected: {exc}"[:500]
        )
        return
    if state["error"] is not None:
        _finalize_chunk(ctx, project_id, source_id, chunk_id, "failed", state["error"])
        return

    trust = "escalated" if state["escalated"] else "trusted"
    accepted = state["accepted"]
    try:
        if accepted:
            embeddings = ctx.router.embed([d.text for d in accepted])
            for draft, embedding in zip(accepted, embeddings):
                record = to_claim_record(
                    draft, chunk_id=chunk_id, source_id=chunk["source_id"], trust=trust
                )
                ctx.graph.upsert_claim(record, embedding, project_id=project_id)
    except ProviderTransientError:
        raise
    except Exception as exc:
        logger.exception("chunk %s commit failed", chunk_id)
        _finalize_chunk(
            ctx, project_id, source_id, chunk_id, "failed", f"unexpected: {exc}"[:500]
        )
        return

    if not accepted and state["rejected"]:
        reasons = state["rejected"][0].get("reasons", [])
        error = "; ".join(reasons) if reasons else "claims rejected"
        _finalize_chunk(ctx, project_id, source_id, chunk_id, "rejected", error[:500])
        return

    error = f"{len(state['rejected'])} claims rejected" if state["rejected"] else None
    _finalize_chunk(ctx, project_id, source_id, chunk_id, "done", error)


@app.task
def synthesize_task(project_id: str = "default"):
    ctx = get_context()
    try:
        dirty = cluster_new_claims(ctx, project_id)
        processed = process_dirty_concepts(ctx, project_id)
        ordered = build_curriculum(ctx, project_id)
    except Exception as exc:
        logger.exception("synthesis failed for project %s", project_id)
        ctx.db.set_project_setting(
            project_id,
            "synthesis:last_error",
            json.dumps({"ts": int(time.time()), "error": str(exc)[:500]}, sort_keys=True),
        )
        raise
    payload = {
        "ts": int(time.time()),
        "dirty_concepts": len(dirty),
        "processed_concepts": processed,
        "curriculum_len": len(ordered),
    }
    ctx.db.set_project_setting(
        project_id, "synthesis:last_run", json.dumps(payload, sort_keys=True)
    )
    ctx.db.set_project_setting(project_id, "synthesis:last_error", "")
