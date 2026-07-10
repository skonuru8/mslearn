import json
import logging
import time

from celery.exceptions import SoftTimeLimitExceeded

from mslearn.adapters.image import image_describe_via_router
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


def _source_prep_stale(db, source_id: str, project_id: str, entry_ts: float) -> bool:
    """True when a chunk_source_task's row is no longer the one it started on.

    Re-read the source row and compare against what the task saw at entry:
    row gone = deleted mid-flight; status flipped away from "chunking" =
    another delivery already processed it (or it was paused/failed); `ts`
    changed = deleted and re-added, i.e. a fresh incarnation whose own task
    owns the work. One INFO line per abort so the event is traceable
    without spamming the worker log.
    """
    row = db.source_row(source_id, project_id=project_id)
    if row is None:
        logger.info("source %s preparation aborted: deleted mid-task", source_id)
        return True
    if row["status"] != "chunking":
        logger.info(
            "source %s preparation aborted: status is %r, not 'chunking'",
            source_id, row["status"],
        )
        return True
    if row["ts"] != entry_ts:
        logger.info("source %s preparation aborted: re-added mid-task", source_id)
        return True
    return False


@app.task(
    bind=True,
    autoretry_for=(ProviderTransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    # A wedged adapter load (e.g. a hung whisper transcription or a frozen
    # model-call socket) must die and release its worker slot instead of
    # occupying it for hours — soft gives the except-clause below a chance
    # to write the source's error state; hard is the backstop.
    soft_time_limit=1800,
    time_limit=2100,
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

    The entry check alone is TOCTOU-racy: load/chunk/embed can run for
    minutes, during which the user may delete the source (row gone) or
    delete-and-re-add it (fresh row, same source_id PK, new `ts`). The row
    is therefore re-checked right before the first write — see
    `_source_row_changed` — so a stale in-flight task can't clobber the
    fresh incarnation or resurrect graph nodes for a deleted source. The
    re-added source's own chunk_source_task (queued by its POST) does the
    real work.
    """
    ctx = get_context()
    row = ctx.db.source_row(source_id, project_id=project_id)
    if row is None or row["status"] != "chunking":
        return
    # Row identity at task start: a re-added source reuses the same PK but
    # gets a fresh `ts`, so a changed ts at re-check means "different
    # incarnation" even if the status looks right.
    entry_ts = row["ts"]

    try:
        doc = load_source(
            ref, source_type=source_type, role=role,
            transcriber=ctx.transcriber,
            describe=image_describe_via_router(ctx.router, ctx.db),
        )
    except SoftTimeLimitExceeded:
        if not _source_prep_stale(ctx.db, source_id, project_id, entry_ts):
            ctx.db.set_source_status(
                source_id, "failed", error="loading exceeded the time limit and was aborted"
            )
        logger.warning("source %s preparation aborted: load exceeded soft time limit", source_id)
        raise
    except Exception as exc:
        if _source_prep_stale(ctx.db, source_id, project_id, entry_ts):
            return
        ctx.db.set_source_status(source_id, "failed", error=str(exc)[:500])
        logger.warning("source %s failed to load %s: %s", source_id, ref, str(exc)[:120])
        return

    chunks = chunk_source(doc)
    try:
        embeddings = ctx.router.embed([c.text for c in chunks]) if chunks else []
    except SoftTimeLimitExceeded:
        if not _source_prep_stale(ctx.db, source_id, project_id, entry_ts):
            ctx.db.set_source_status(
                source_id, "failed", error="embedding exceeded the time limit and was aborted"
            )
        logger.warning(
            "source %s preparation aborted: embedding exceeded soft time limit", source_id
        )
        raise
    except ProviderTransientError as exc:
        if self.request.retries >= self.max_retries:
            if _source_prep_stale(ctx.db, source_id, project_id, entry_ts):
                return
            ctx.db.set_source_status(
                source_id, "failed", error=f"embedding retries exhausted: {exc}"[:500]
            )
            logger.warning("source %s failed: embedding retries exhausted", source_id)
        raise
    except Exception as exc:
        if _source_prep_stale(ctx.db, source_id, project_id, entry_ts):
            return
        ctx.db.set_source_status(source_id, "failed", error=str(exc)[:500])
        logger.warning("source %s failed to embed chunks: %s", source_id, str(exc)[:120])
        return

    # TOCTOU re-check: the slow work above (adapter load can take minutes)
    # may have raced a delete or delete+re-add. Abort before the first
    # graph/DB write if this task's row incarnation is no longer current.
    if _source_prep_stale(ctx.db, source_id, project_id, entry_ts):
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


def _write_synthesis_progress(db, project_id: str, *, phase: str, done: int, total: int, ts: float) -> None:
    db.set_project_setting(
        project_id,
        "synthesis:progress",
        json.dumps({"phase": phase, "done": done, "total": total, "ts": int(ts)}, sort_keys=True),
    )


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
    # See chunk_source_task: a wedged model call must release its slot
    # instead of occupying it for hours.
    soft_time_limit=1800,
    time_limit=2100,
)
def extract_chunk_task(self, project_id: str, chunk_id: str):
    ctx = get_context()
    source_id = chunk_id.rsplit(":", 1)[0]

    source = ctx.db.source_row(source_id, project_id=project_id)
    if source is None:
        # Source deleted after this chunk was enqueued. Say so once and stop
        # before any model call — previously the task silently ran a full
        # extraction, then no-oped inside mark_chunk's missing-row early
        # return, burning tokens for a source that no longer exists.
        logger.info("chunk %s skipped: source deleted", chunk_id)
        return
    if source["status"] == "paused":
        ctx.db.mark_chunk(chunk_id, "skipped_paused")
        return

    chunk = ctx.graph.get_chunk(chunk_id, project_id=project_id)
    if chunk is None:
        _finalize_chunk(ctx, project_id, source_id, chunk_id, "failed", "chunk not found in graph")
        return

    try:
        state = run_extraction(ctx.extraction_graph, chunk_id, chunk["text"])
    except SoftTimeLimitExceeded:
        _finalize_chunk(
            ctx, project_id, source_id, chunk_id, "failed",
            "extraction exceeded the time limit and was aborted",
        )
        raise
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
    if ctx.graph.source_type_of(chunk["source_id"], project_id=project_id) == "image":
        # Image claims are model-read from pixels, not verbatim quotes from an
        # authored text. Still trust-gated (the quote must match the
        # transcription), but tiered so provenance stays visible.
        trust = "image_observed"
    accepted = state["accepted"]
    try:
        if accepted:
            # The trust gate in validate() already embedded every accepted
            # claim's text once (see extraction_graph.claim_embeddings) —
            # reuse those vectors instead of re-embedding here. Only texts
            # that somehow aren't in the map (should be rare/never) get a
            # single fallback embed call, batched together.
            claim_embeddings = state.get("claim_embeddings", {})
            missing_texts = [d.text for d in accepted if d.text not in claim_embeddings]
            if missing_texts:
                fresh = ctx.router.embed(missing_texts)
                claim_embeddings = {**claim_embeddings, **dict(zip(missing_texts, fresh))}
            for draft in accepted:
                record = to_claim_record(
                    draft, chunk_id=chunk_id, source_id=chunk["source_id"], trust=trust
                )
                ctx.graph.upsert_claim(record, claim_embeddings[draft.text], project_id=project_id)
    except SoftTimeLimitExceeded:
        _finalize_chunk(
            ctx, project_id, source_id, chunk_id, "failed",
            "commit exceeded the time limit and was aborted",
        )
        raise
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
    # A wedged model call (the incident: one request survived the 600s httpx
    # read timeout entirely, apparently a machine-sleep-frozen socket) must
    # not occupy a worker slot indefinitely — die and free it instead.
    soft_time_limit=3600,
    time_limit=3900,
)
def synthesize_task(project_id: str = "default"):
    ctx = get_context()
    # Clear the queued marker as soon as the run actually starts (not just
    # when it finishes) — the task is incremental/idempotent, so a trigger
    # that lands after this point starts a fresh queued run rather than
    # being wrongly absorbed into the one already in flight.
    ctx.db.clear_synthesis_queued(project_id)
    now = time.time()
    ctx.db.set_project_setting(project_id, "synthesis:running_since", str(int(now)))
    # Grouping/ordering are single model-call-per-item at most and don't have
    # a cheap upfront total, so they report phase only; process_dirty_concepts
    # (below) writes its own n-of-m progress every concept — it runs two
    # model calls per concept and dominated the 78-minute incident run.
    _write_synthesis_progress(ctx.db, project_id, phase="grouping", done=0, total=0, ts=now)
    logger.info("synthesis started project=%s", project_id)
    try:
        dirty = cluster_new_claims(ctx, project_id)
        # Heartbeat between phases: a long-but-alive run (the 107-chunk video
        # incident ran 78+ minutes) must keep refreshing running_since so the
        # status endpoint's abandoned-build self-heal never trips on a run
        # that's still making progress.
        now = time.time()
        ctx.db.set_project_setting(project_id, "synthesis:running_since", str(int(now)))
        processed = process_dirty_concepts(ctx, project_id)
        now = time.time()
        ctx.db.set_project_setting(project_id, "synthesis:running_since", str(int(now)))
        _write_synthesis_progress(ctx.db, project_id, phase="ordering", done=0, total=0, ts=now)
        ordered = build_curriculum(ctx, project_id)
    except SoftTimeLimitExceeded:
        logger.warning("synthesis exceeded soft time limit for project %s", project_id)
        ctx.db.set_project_setting(
            project_id,
            "synthesis:last_error",
            json.dumps(
                {"ts": int(time.time()), "error": "synthesis exceeded the time limit and was aborted"},
                sort_keys=True,
            ),
        )
        ctx.db.set_project_setting(project_id, "synthesis:running_since", "")
        ctx.db.set_project_setting(project_id, "synthesis:progress", "")
        raise
    except Exception as exc:
        logger.exception("synthesis failed for project %s", project_id)
        ctx.db.set_project_setting(
            project_id,
            "synthesis:last_error",
            json.dumps({"ts": int(time.time()), "error": str(exc)[:500]}, sort_keys=True),
        )
        ctx.db.set_project_setting(project_id, "synthesis:running_since", "")
        ctx.db.set_project_setting(project_id, "synthesis:progress", "")
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
    ctx.db.set_project_setting(project_id, "synthesis:progress", "")
    logger.info(
        "synthesis done project=%s processed=%d curriculum=%d",
        project_id, processed, len(ordered),
    )
