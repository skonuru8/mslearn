from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from mslearn.server.deps import get_ctx, get_project_id

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
def list_memory(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    memory = _memory_or_503(ctx)
    try:
        return {"items": [asdict(item) for item in memory.all(project_id=project_id)]}
    except Exception as exc:
        raise _unavailable(exc) from exc


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)
):
    memory = _memory_or_503(ctx)
    try:
        memory.delete(memory_id)
    except Exception as exc:
        raise _unavailable(exc) from exc
    return {"memory_id": memory_id, "deleted": True}


def _memory_or_503(ctx):
    if ctx.memory is None:
        raise HTTPException(status_code=503, detail="learner memory unavailable")
    return ctx.memory


def _unavailable(exc: Exception) -> HTTPException:
    # SqliteMemory (memory/sqlite_memory.py) can still fail on real use — an
    # unreachable embedder surfaces here, not at ctx.memory construction
    # time. Without this, that raised as an unhandled 500 instead of the
    # same honest "memory unavailable" the UI already knows how to show.
    return HTTPException(status_code=503, detail=f"learner memory unavailable: {exc}"[:500])
