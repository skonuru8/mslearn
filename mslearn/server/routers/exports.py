from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from mslearn.pipeline.exports import export_anki, export_graph, export_markdown
from mslearn.server.deps import get_ctx

router = APIRouter(prefix="/api/exports", tags=["exports"])

ExportKind = Literal["markdown", "anki", "graph"]


class ExportRequest(BaseModel):
    kinds: list[ExportKind] = Field(default_factory=lambda: ["markdown", "anki", "graph"])


@router.post("")
def create_export(body: ExportRequest, ctx=Depends(get_ctx)):
    out_dir = Path("data") / "exports" / _timestamp()
    files: dict[str, list[str]] = {}
    for kind in body.kinds:
        if kind == "markdown":
            paths = export_markdown(ctx, out_dir / "markdown")
        elif kind == "anki":
            paths = [export_anki(ctx, out_dir / "mslearn.apkg")]
        else:
            paths = export_graph(ctx, out_dir / "graph")
        files[kind] = [path.as_posix() for path in paths]
    return {"root": out_dir.as_posix(), "files": files}


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
