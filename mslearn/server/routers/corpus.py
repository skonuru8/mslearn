import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from mslearn.opsdb import SYNTHESIS_RUNNING_TTL_S
from mslearn.pipeline.orchestrator import IngestError, ingest_source, resume_pending
from mslearn.prompts import get_domain_profile
from mslearn.server.deps import get_ctx, get_project_id
from mslearn.worker.app import app as celery_app
from mslearn.worker.app import worker_online
from mslearn.worker.tasks import try_enqueue_synthesis

router = APIRouter(prefix="/api/corpus", tags=["corpus"])

VALID_PROFILES = frozenset({"technical", "interpretive"})


class IngestRequest(BaseModel):
    ref: str
    role: str
    source_type: str | None = None
    local: bool = False


class DomainProfileUpdate(BaseModel):
    profile: str


@contextmanager
def _local_eager():
    """Run Celery tasks inline when local=true.

    NOTE: this mutates the process-global `celery_app.conf.task_always_eager`
    for the duration of the request, which is racy under concurrent requests
    (request B enqueues while A holds eager=True -> B's tasks run inline too,
    or A restores False mid-B). `local=true` is intended for tests and the
    CLI (single-threaded, one request/process at a time) only — the primary
    UI no longer offers it (background ingestion is the default; see
    scripts/dev_up.sh / `make run` for the worker + API processes).
    """
    prev = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prev


@router.post("/sources")
def create_source(
    body: IngestRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    try:
        if body.local:
            with _local_eager():
                source_id = ingest_source(
                    body.ref,
                    role=body.role,
                    source_type=body.source_type,
                    enqueue=True,
                    project_id=project_id,
                )
        else:
            source_id = ingest_source(
                body.ref,
                role=body.role,
                source_type=body.source_type,
                enqueue=True,
                project_id=project_id,
            )
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"source_id": source_id}


_UPLOAD_SUFFIXES = frozenset(
    {".pdf", ".epub", ".html", ".htm", ".mp3", ".m4a", ".wav", ".flac", ".ogg"}
)
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB — a multi-GB upload must not fill the disk


def _upload_dir(ctx) -> Path:
    data_dir = getattr(getattr(ctx, "settings", None), "data_dir", None) or Path("data")
    return Path(data_dir) / "uploads"


def _copy_with_size_cap(src, dest: Path, max_bytes: int) -> None:
    """Stream-copy `src` into `dest`, aborting with a 413 if it exceeds `max_bytes`."""
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds the {max_bytes // (1024 * 1024)} MB upload limit",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise


@router.post("/upload")
def upload_source(
    file: UploadFile = File(...),
    role: str = Form("supplement"),
    local: bool = Form(False),
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    original = Path(file.filename or "upload").name
    suffix = Path(original).suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type {suffix!r}; allowed: {sorted(_UPLOAD_SUFFIXES)}",
        )
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip("-") or "upload"
    dest_dir = _upload_dir(ctx)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{int(time.time())}-{stem}{suffix}"
    _copy_with_size_cap(file.file, dest, _MAX_UPLOAD_BYTES)

    try:
        if local:
            with _local_eager():
                source_id = ingest_source(str(dest), role=role, enqueue=True, project_id=project_id)
        else:
            source_id = ingest_source(str(dest), role=role, enqueue=True, project_id=project_id)
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"source_id": source_id, "stored_path": str(dest)}


@router.get("/sources")
def list_sources(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return ctx.db.all_sources(project_id)


@router.post("/sources/{source_id}/pause")
def pause_source(source_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    if ctx.db.source_row(source_id, project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    ctx.db.set_source_status(source_id, "paused")
    return {"source_id": source_id, "status": "paused"}


@router.post("/sources/{source_id}/resume")
def resume_source(source_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    if ctx.db.source_row(source_id, project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    ctx.db.set_source_status(source_id, "running", clear_error=True)
    resumed = resume_pending(project_id)
    return {"source_id": source_id, "status": "running", "resumed_chunks": resumed}


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    if ctx.db.source_row(source_id, project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    affected = ctx.graph.delete_source(source_id, project_id=project_id)
    ctx.db.delete_source(source_id, project_id=project_id)
    if affected:
        # curriculum + surviving dirty concepts need a rebuild pass; deduped
        # so deleting several sources in a row queues one run, not one each
        try_enqueue_synthesis(ctx.db, project_id)
    return {"source_id": source_id, "deleted": True, "affected_concepts": len(affected)}


@router.get("/sources/{source_id}/failures")
def source_failures(source_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    if ctx.db.source_row(source_id, project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    return ctx.db.failure_groups(source_id)


@router.post("/sources/{source_id}/retry-failed")
def retry_failed_source(
    source_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)
):
    if ctx.db.source_row(source_id, project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    reset_ids = ctx.db.reset_failed_chunks(source_id)
    ctx.db.set_source_status(source_id, "running", clear_error=True)
    resume_pending(project_id)
    return {"source_id": source_id, "status": "running", "retried_chunks": len(reset_ids)}


@router.get("/settings/domain-profile")
def get_domain_profile_endpoint(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return {"profile": get_domain_profile(ctx.db, project_id)}


@router.post("/settings/domain-profile")
def set_domain_profile(
    body: DomainProfileUpdate, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)
):
    if body.profile not in VALID_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=f"profile must be one of {sorted(VALID_PROFILES)}",
        )
    ctx.db.set_project_setting(project_id, "corpus.domain_profile", body.profile)
    return {"profile": body.profile}


@router.post("/synthesize")
def enqueue_synthesis(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    enqueued = try_enqueue_synthesis(ctx.db, project_id)
    return {
        "enqueued": enqueued,
        "already_running": not enqueued,
        "worker_online": worker_online(),
    }


@router.get("/synthesis/status")
def synthesis_status(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    raw = ctx.db.get_project_setting(project_id, "synthesis:last_run")
    raw_error = ctx.db.get_project_setting(project_id, "synthesis:last_error")
    running_since = ctx.db.get_project_setting(project_id, "synthesis:running_since")
    raw_progress = ctx.db.get_project_setting(project_id, "synthesis:progress")
    last_run = json.loads(raw) if raw is not None else None
    last_error = json.loads(raw_error) if raw_error else None
    running_ts = int(running_since) if running_since else None
    progress = json.loads(raw_progress) if raw_progress else None

    # Self-heal an abandoned build: a worker that died or was force-restarted
    # mid-run leaves `running_since` set forever, so the UI would show
    # "Building your course..." indefinitely with no way to tell live from
    # wedged. A marker older than 2x the dedup TTL can't belong to a run that
    # is still alive (synthesize_task heartbeats it between phases), so treat
    # it as abandoned: clear it and, unless a later last_run already
    # supersedes it (a completed run whose own clear just didn't land), tell
    # the user the build was interrupted. Never overwrite the DB's last_error
    # — this synthetic value is response-only.
    if running_ts is not None and time.time() - running_ts > 2 * SYNTHESIS_RUNNING_TTL_S:
        ctx.db.set_project_setting(project_id, "synthesis:running_since", "")
        ctx.db.set_project_setting(project_id, "synthesis:progress", "")
        fresher_run = last_run is not None and last_run.get("ts", 0) >= running_ts
        if not fresher_run:
            last_error = {
                "ts": int(time.time()),
                "error": "course build was interrupted — press Build to restart",
            }
        running_ts = None
        progress = None

    return {
        "last_run": last_run,
        "last_error": last_error,
        "running_since": running_ts,
        "progress": progress,
    }
