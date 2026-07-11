from __future__ import annotations

import json
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


def _pending_row(row: dict) -> dict:
    proposal = json.loads(row["proposal_json"])
    return {
        "run_id": row["id"],
        "ts": row["ts"],
        "proposal": proposal,
        "shadow_before": json.loads(row["shadow_before_json"]) if row["shadow_before_json"] else None,
        "shadow_after": json.loads(row["shadow_after_json"]) if row["shadow_after_json"] else None,
        "why": row["reason"] or proposal.get("why", ""),
    }


@router.get("/pending")
def pending_runs(ctx=Depends(get_ctx)):
    return [_pending_row(row) for row in ctx.db.pending_evolution_runs()]


def _find_pending_or_404(ctx, run_id: int) -> dict:
    row = next((r for r in ctx.db.pending_evolution_runs() if r["id"] == run_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no pending evolution run {run_id}")
    return row


@router.post("/pending/{run_id}/approve")
def approve_pending(run_id: int, ctx=Depends(get_ctx)):
    row = _find_pending_or_404(ctx, run_id)
    proposal = json.loads(row["proposal_json"])
    prompt_name = str(proposal.get("key", "")).removeprefix("prompt:")
    ctx.db.set_setting(f"prompt:{prompt_name}", str(proposal.get("new_prompt", "")))
    ctx.db.set_evolution_run_status(run_id, "applied")
    return {"run_id": run_id, "status": "applied"}


@router.post("/pending/{run_id}/reject")
def reject_pending(run_id: int, ctx=Depends(get_ctx)):
    _find_pending_or_404(ctx, run_id)
    ctx.db.set_evolution_run_status(run_id, "rejected")
    return {"run_id": run_id, "status": "rejected"}
