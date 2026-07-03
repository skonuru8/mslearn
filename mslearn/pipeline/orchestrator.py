from pathlib import Path

from mslearn.adapters.registry import load_source
from mslearn.chunking import chunk_source
from mslearn.opsdb import DEFAULT_PROJECT_ID
from mslearn.worker.context import get_context
from mslearn.worker.tasks import extract_chunk_task


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
    ctx = get_context()
    try:
        doc = load_source(ref, source_type=source_type, role=role)
    except Exception as exc:
        from mslearn.adapters.base import make_source_id

        source_id = make_source_id(ref)
        ctx.db.register_source(
            source_id, ref=ref, role=role, total_chunks=0, project_id=project_id
        )
        ctx.db.set_source_status(source_id, "failed", error=str(exc)[:500])
        raise IngestError(f"failed to load {ref!r}: {exc}") from exc

    chunks = chunk_source(doc)
    embeddings = ctx.router.embed([c.text for c in chunks]) if chunks else []
    ctx.graph.upsert_source(doc, project_id=project_id)
    ctx.graph.upsert_chunks(chunks, embeddings, project_id=project_id)
    ctx.db.register_source(
        doc.source_id, ref=ref, role=role, total_chunks=len(chunks), project_id=project_id
    )
    ctx.db.register_chunk_jobs(doc.source_id, [c.chunk_id for c in chunks], project_id=project_id)
    ctx.db.set_source_status(doc.source_id, "running")
    if enqueue:
        for chunk in chunks:
            extract_chunk_task.delay(project_id, chunk.chunk_id)
    return doc.source_id


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
