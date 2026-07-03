from fastapi import Header, HTTPException, Request

from mslearn.opsdb import DEFAULT_PROJECT_ID
from mslearn.worker.context import PipelineContext


def get_ctx(request: Request) -> PipelineContext:
    return request.app.state.context


def get_project_id(
    request: Request,
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> str:
    ctx = get_ctx(request)
    project_id = (x_project_id or DEFAULT_PROJECT_ID).strip() or DEFAULT_PROJECT_ID
    if not ctx.db.project_exists(project_id):
        raise HTTPException(status_code=404, detail=f"unknown project {project_id!r}")
    return project_id
