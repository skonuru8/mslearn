import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from mslearn.pipeline.orchestrator import IngestError, ingest_source, resume_pending
from mslearn.prompts import get_domain_profile
from mslearn.server.deps import get_ctx
from mslearn.worker.app import app as celery_app
from mslearn.worker.tasks import synthesize_task

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
    """Run Celery tasks inline when local=true (dev/tests); production uses workers."""
    prev = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prev


@router.post("/sources")
def create_source(body: IngestRequest, ctx=Depends(get_ctx)):
    try:
        if body.local:
            with _local_eager():
                source_id = ingest_source(
                    body.ref,
                    role=body.role,
                    source_type=body.source_type,
                    enqueue=True,
                )
        else:
            source_id = ingest_source(
                body.ref,
                role=body.role,
                source_type=body.source_type,
                enqueue=True,
            )
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"source_id": source_id}


_UPLOAD_SUFFIXES = frozenset(
    {".pdf", ".epub", ".html", ".htm", ".mp3", ".m4a", ".wav", ".flac", ".ogg"}
)


def _upload_dir(ctx) -> Path:
    data_dir = getattr(getattr(ctx, "settings", None), "data_dir", None) or Path("data")
    return Path(data_dir) / "uploads"


@router.post("/upload")
def upload_source(
    file: UploadFile = File(...),
    role: str = Form("supplement"),
    local: bool = Form(False),
    ctx=Depends(get_ctx),
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
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        if local:
            with _local_eager():
                source_id = ingest_source(str(dest), role=role, enqueue=True)
        else:
            source_id = ingest_source(str(dest), role=role, enqueue=True)
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"source_id": source_id, "stored_path": str(dest)}


@router.get("/sources")
def list_sources(ctx=Depends(get_ctx)):
    return ctx.db.all_sources()


@router.post("/sources/{source_id}/pause")
def pause_source(source_id: str, ctx=Depends(get_ctx)):
    if ctx.db.source_row(source_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    ctx.db.set_source_status(source_id, "paused")
    return {"source_id": source_id, "status": "paused"}


@router.post("/sources/{source_id}/resume")
def resume_source(source_id: str, ctx=Depends(get_ctx)):
    if ctx.db.source_row(source_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    ctx.db.set_source_status(source_id, "running")
    resumed = resume_pending()
    return {"source_id": source_id, "status": "running", "resumed_chunks": resumed}


@router.get("/settings/domain-profile")
def get_domain_profile_endpoint(ctx=Depends(get_ctx)):
    return {"profile": get_domain_profile(ctx.db)}


@router.post("/settings/domain-profile")
def set_domain_profile(body: DomainProfileUpdate, ctx=Depends(get_ctx)):
    if body.profile not in VALID_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=f"profile must be one of {sorted(VALID_PROFILES)}",
        )
    ctx.db.set_setting("corpus.domain_profile", body.profile)
    return {"profile": body.profile}


@router.post("/synthesize")
def enqueue_synthesis(ctx=Depends(get_ctx)):
    synthesize_task.delay()
    return {"enqueued": True}
