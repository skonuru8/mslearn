from pathlib import Path

from mslearn.adapters.base import make_source_id
from mslearn.opsdb import DEFAULT_PROJECT_ID
from mslearn.worker.context import get_context
from mslearn.worker.tasks import chunk_source_task, extract_chunk_task


class IngestError(Exception):
    """A source could not be ingested; recorded in ingest_sources."""


def ingest_source(
    ref: str,
    *,
    role: str = "supplement",
    source_type: str | None = None,
    enqueue: bool = True,
    project_id: str = DEFAULT_PROJECT_ID,
) -> str:
    """Register a source and hand loading off to chunk_source_task.

    Returns immediately: the row starts in status "chunking" with
    total_chunks=0 (the "Preparing…" state in the UI). chunk_source_task
    does the actual adapter load -> chunk -> embed -> graph upsert ->
    extract-enqueue work in the background, so a slow load (e.g. YouTube
    without captions: yt_dlp download + whisper transcription, on the
    order of minutes) no longer blocks the HTTP request.

    In eager/test mode (Celery task_always_eager — `local=true` requests,
    ingest_cli --local, most existing tests) chunk_source_task runs inline
    before this call returns, so callers relying on synchronous completion
    still see the fully processed row.
    """
    ctx = get_context()
    source_id = make_source_id(ref)
    ctx.db.register_source(source_id, ref=ref, role=role, total_chunks=0, project_id=project_id)
    ctx.db.set_source_status(source_id, "chunking")
    # `enqueue` only controls whether chunk_source_task schedules the
    # per-chunk extract_chunk_task calls once loading/chunking finishes —
    # loading/chunking/embedding themselves always happen in the background.
    chunk_source_task.delay(project_id, source_id, ref, role, source_type, enqueue)
    return source_id


def order_corpus(refs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    def size_key(ref: str) -> tuple[int, int]:
        path = Path(ref)
        if path.exists():
            return (0, path.stat().st_size)
        return (1, 0)  # non-file refs (URLs) after files, insertion-stable

    spines = [r for r in refs if r[1] == "spine"]
    supplements = sorted(
        (r for r in refs if r[1] != "spine"), key=lambda r: size_key(r[0])
    )
    return spines + supplements


def enqueue_corpus(
    refs: list[tuple[str, str]], *, project_id: str = DEFAULT_PROJECT_ID
) -> list[str]:
    source_ids = []
    for ref, role in order_corpus(refs):
        try:
            source_ids.append(ingest_source(ref, role=role, project_id=project_id))
        except IngestError:
            continue  # recorded as failed; never blocks the rest of the corpus
    return source_ids


def resume_pending(project_id: str | None = None) -> int:
    ctx = get_context()
    count = 0
    if project_id is None:
        sources: list[dict] = []
        for proj in ctx.db.list_projects():
            sources.extend(ctx.db.all_sources(proj["project_id"]))
    else:
        sources = ctx.db.all_sources(project_id)
    for source in sources:
        pid = source["project_id"]
        if source["status"] in ("paused", "done", "failed"):
            continue
        for chunk_id in ctx.db.pending_chunks(source["source_id"]):
            extract_chunk_task.delay(pid, chunk_id)
            count += 1
    return count
