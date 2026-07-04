import json
import logging
import time

from mslearn.adapters.registry import load_source
from mslearn.chunking import chunk_source
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


@app.task(
    bind=True,
    autoretry_for=(ProviderTransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def chunk_source_task(
    self,
    project_id: str,
    source_id: str,
    ref: str,
    role: str,
    source_type: str | None = None,
    enqueue: bool = True,
):
    """Load, chunk, embed, and graph-upsert a source, then enqueue extraction.

    Split out of orchestrator.ingest_source so the create-source HTTP
    request can return immediately: the row starts in status "chunking"
    (total_chunks=0, rendered as "Preparing…") and this task does the slow
    work — adapter load (which for YouTube-without-captions means a yt_dlp
    download + whisper transcription, on the order of minutes) through
    chunking, embedding, and the Neo4j upserts.

    Guarded against redelivery/delete races: a source whose status is no
    longer "chunking" (already processed by an earlier delivery of this
    same task, or deleted out from under it) is a silent no-op — mirrors
    the paused-skip guard in extract_chunk_task. register_chunk_jobs
    (INSERT OR IGNORE) and upsert_chunks (MERGE) make a retried task safe
    even if it re-runs after partially registering chunks.
    """
    ctx = get_context()
    row = ctx.db.source_row(source_id, project_id=project_id)
    if row is None or row["status"] != "chunking":
        return

    try:
        doc = load_source(ref, source_type=source_type, role=role)
    except Exception as exc:
        ctx.db.set_source_status(source_id, "failed", error=str(exc)[:500])
        logger.warning("source %s failed to load %s: %s", source_id, ref, str(exc)[:120])
        return

    chunks = chunk_source(doc)
    try:
        embeddings = ctx.router.embed([c.text for c in chunks]) if chunks else []
    except ProviderTransientError as exc:
        if self.request.retries >= self.max_retries:
            ctx.db.set_source_status(
                source_id, "failed", error=f"embedding retries exhausted: {exc}"[:500]
            )
            logger.warning("source %s failed: embedding retries exhausted", source_id)
        raise
    except Exception as exc:
        ctx.db.set_source_status(source_id, "failed", error=str(exc)[:500])
        logger.warning("source %s failed to embed chunks: %s", source_id, str(exc)[:120])
        return

    ctx.graph.upsert_source(doc, project_id=project_id)
    ctx.graph.upsert_chunks(chunks, embeddings, project_id=project_id)
    ctx.db.register_chunk_jobs(source_id, [c.chunk_id for c in chunks], project_id=project_id)
    ctx.db.set_source_total_chunks(source_id, len(chunks))
    ctx.db.set_source_status(source_id, "running")
    logger.info("source registered id=%s ref=%s chunks=%d", source_id, ref, len(chunks))
    if enqueue:
        for chunk in chunks:
            extract_chunk_task.delay(project_id, chunk.chunk_id)


def try_enqueue_synthesis(db, project_id: str) -> bool:
    """Single choke point every synthesis trigger routes through.

    Pre-fix, the Build button, delete_source's rebuild, and
    try_complete_source's auto-fire each called `synthesize_task.delay()`
    directly with zero dedup — three near-simultaneous triggers could stack
    three multi-minute runs, occupying worker slots. Returns True
    (and enqueues) only when `db.try_mark_synthesis_queued` grants the
    marker; False means a run is already queued or running and this trigger
    was absorbed into it.
    """
    if not db.try_mark_synthesis_queued(project_id):
        return False
    synthesize_task.delay(project_id)
    return True


def _check_failure_monitor(db, source_id: str) -> None:
    stats = db.failure_stats(source_id)
    min_chunks = int(db.get_tunable("monitor.min_chunks"))
    threshold = db.get_tunable("monitor.failure_rate_threshold")
    if stats["total"] >= min_chunks and stats["problems"] / stats["total"] > threshold:
        error = (
            f"failure rate {stats['problems']}/{stats['total']}"
            f" ({stats['failed']} failed, {stats['rejected']} rejected)"
        )
        db.set_source_status(source_id, "paused", error=error)
        logger.warning("source %s paused: %s", source_id, error)


def _finalize_chunk(
    ctx, project_id: str, source_id: str, chunk_id: str, status: str, error: str | None = None
) -> None:
    # Single choke point every chunk outcome passes through — the one place
    # worth an event line instead of scattering logging across every caller.
    if status in ("failed", "rejected"):
        logger.warning("chunk %s %s: %s", chunk_id, status, (error or "")[:160])
    else:
        logger.info("chunk %s %s", chunk_id, status)
    ctx.db.mark_chunk(chunk_id, status, error=error)
    if status in ("failed", "rejected"):
        _check_failure_monitor(ctx.db, source_id)
    if ctx.db.try_complete_source(source_id):
        try_enqueue_synthesis(ctx.db, project_id)


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


@app.task(
    autoretry_for=(ProviderTransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def synthesize_task(project_id: str = "default"):
    ctx = get_context()
    # Clear the queued marker as soon as the run actually starts (not just
    # when it finishes) — the task is incremental/idempotent, so a trigger
    # that lands after this point starts a fresh queued run rather than
    # being wrongly absorbed into the one already in flight.
    ctx.db.clear_synthesis_queued(project_id)
    ctx.db.set_project_setting(
        project_id, "synthesis:running_since", str(int(time.time()))
    )
    logger.info("synthesis started project=%s", project_id)
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
        ctx.db.set_project_setting(project_id, "synthesis:running_since", "")
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
    ctx.db.set_project_setting(project_id, "synthesis:running_since", "")
    logger.info(
        "synthesis done project=%s processed=%d curriculum=%d",
        project_id, processed, len(ordered),
    )
