from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from mslearn.server.deps import get_ctx, get_project_id

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
def list_memory(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    memory = _memory_or_503(ctx)
    return {"items": [asdict(item) for item in memory.all(project_id=project_id)]}


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)
):
    memory = _memory_or_503(ctx)
    memory.delete(memory_id)
    return {"memory_id": memory_id, "deleted": True}


def _memory_or_503(ctx):
    if ctx.memory is None:
        raise HTTPException(status_code=503, detail="learner memory unavailable")
    return ctx.memory
