import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mslearn.opsdb import DEFAULT_PROJECT_ID
from mslearn.server.deps import get_ctx

router = APIRouter(prefix="/api/projects", tags=["projects"])

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project_id: str | None = None


@router.get("")
def list_projects(ctx=Depends(get_ctx)):
    return ctx.db.list_projects()


@router.post("")
def create_project(body: ProjectCreate, ctx=Depends(get_ctx)):
    project_id = body.project_id or _slugify(body.name)
    if not _PROJECT_ID_RE.match(project_id):
        raise HTTPException(
            status_code=422,
            detail="project_id must be lowercase letters, digits, hyphens, underscores",
        )
    if ctx.db.project_exists(project_id):
        raise HTTPException(status_code=409, detail=f"project {project_id!r} already exists")
    ctx.db.create_project(project_id, body.name.strip())
    return {"project_id": project_id, "name": body.name.strip()}


@router.delete("/{project_id}")
def delete_project(project_id: str, ctx=Depends(get_ctx)):
    if project_id == DEFAULT_PROJECT_ID:
        raise HTTPException(status_code=422, detail="cannot delete the default project")
    if not ctx.db.project_exists(project_id):
        raise HTTPException(status_code=404, detail=f"unknown project {project_id!r}")
    ctx.graph.delete_project(project_id)
    ctx.db.delete_project(project_id)
    return {"deleted": project_id}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:63] or "project"
