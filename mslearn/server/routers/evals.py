from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mslearn.evals.evolve import evolve_once
from mslearn.evals.golden import (
    GOLDEN_KINDS,
    delete_golden_record,
    load_golden,
    replace_golden_record,
)
from mslearn.evals.runner import run_eval
from mslearn.evals.seed import pending_golden, seed_clustering, seed_extraction, seed_grounding, seed_tension
from mslearn.server.deps import get_ctx

router = APIRouter(prefix="/api/evals", tags=["evals"])


class GoldenReviewRequest(BaseModel):
    action: Literal["approve", "correct", "reject"]
    corrected: dict[str, Any] | None = None


@router.get("/golden/{kind}")
def golden_queue(kind: str, status: str = "pending"):
    if kind not in GOLDEN_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown golden kind {kind!r}")
    rows = load_golden(kind)
    return [
        {"index": index, **asdict(row)}
        for index, row in enumerate(rows)
        if row.review == status
    ]


@router.post("/golden/{kind}/{index}")
def review_golden(kind: str, index: int, body: GoldenReviewRequest):
    if kind not in GOLDEN_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown golden kind {kind!r}")
    rows = load_golden(kind)
    if index < 0 or index >= len(rows):
        raise HTTPException(status_code=404, detail=f"golden index out of range: {index}")
    if body.action == "reject":
        delete_golden_record(kind, index)
        return {"index": index, "status": "rejected"}
    row = rows[index]
    payload = asdict(row)
    if body.action == "correct":
        if body.corrected:
            payload.update(body.corrected)
        payload["review"] = "corrected"
    else:
        payload["review"] = "approved"
    cls = type(row)
    replace_golden_record(kind, index, cls(**payload))
    return {"index": index, "status": payload["review"]}


@router.post("/seed/{kind}")
def seed_kind(kind: str, ctx=Depends(get_ctx)):
    if kind == "extraction":
        added = seed_extraction(ctx)
    elif kind == "grounding":
        added = seed_grounding(ctx)
    elif kind == "clustering":
        added = seed_clustering(ctx)
    elif kind == "tension":
        added = seed_tension(ctx)
    else:
        raise HTTPException(status_code=404, detail=f"unknown seed kind {kind!r}")
    return {"kind": kind, "added": added, "pending": len(pending_golden(kind))}


@router.get("/report")
def eval_report(ctx=Depends(get_ctx)):
    latest = ctx.db.latest_eval_run()
    if latest is None:
        return {"run": None, "metrics": []}
    return {
        "run": latest,
        "metrics": ctx.db.eval_metrics_for_run(latest["id"]),
    }


@router.get("/history")
def eval_history(limit: int = 20, ctx=Depends(get_ctx)):
    return ctx.db.eval_history(limit=limit)


@router.post("/run")
def trigger_eval(offline: bool = True, ctx=Depends(get_ctx)):
    result = run_eval(ctx, offline=offline)
    return result


@router.post("/evolve")
def trigger_evolve(ctx=Depends(get_ctx)):
    return evolve_once(ctx)


@router.get("/evolve/history")
def evolve_history(limit: int = 20, ctx=Depends(get_ctx)):
    return ctx.db.evolution_history(limit=limit)
