from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mslearn.pipeline.quiz import generate_question, grade_answer, next_concept, public_quiz_stats
from mslearn.pipeline.teaching import TeachingError, generate_teaching
from mslearn.server.deps import get_ctx
from mslearn.worker.tasks import synthesize_task

router = APIRouter(prefix="/api/study", tags=["study"])
quiz_router = APIRouter(prefix="/api/quiz", tags=["quiz"])


class FlagRequest(BaseModel):
    reason: str


class QuizAnswerRequest(BaseModel):
    concept_id: str
    answer: str


@router.get("/curriculum")
def curriculum(ctx=Depends(get_ctx)):
    return ctx.graph.curriculum()


@router.get("/concepts/{concept_id}")
def concept(concept_id: str, ctx=Depends(get_ctx)):
    concept_row = ctx.graph.get_concept(concept_id)
    if concept_row is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    claims = [
        c
        for c in ctx.graph.claims_in_concept(concept_id)
        if c.get("trust", "trusted") in {"trusted", "escalated"}
    ]
    return {
        "concept": concept_row,
        "claims": claims,
        "conflicts": ctx.graph.conflicts_in_concept(concept_id),
        "citations": ctx.graph.citations_for_claims([row["claim_id"] for row in claims]),
    }


@router.get("/concepts/{concept_id}/teach")
def teach(concept_id: str, force: bool = False, ctx=Depends(get_ctx)):
    if ctx.graph.get_concept(concept_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    try:
        markdown = generate_teaching(ctx, concept_id, force=force)
    except TeachingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {"markdown": markdown}


@router.post("/claims/{claim_id}/flag")
def flag_claim(claim_id: str, body: FlagRequest, ctx=Depends(get_ctx)):
    concept_id = ctx.graph.concept_id_of_claim(claim_id)
    if concept_id is None:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id!r}")
    ctx.graph.set_claim_trust(claim_id, "rejected")
    ctx.graph.mark_concept_dirty(concept_id, True)
    ctx.graph.set_concept_teaching(concept_id, "")
    synthesize_task.delay()
    return {"claim_id": claim_id, "concept_id": concept_id, "status": "flagged"}


@quiz_router.get("/next")
def quiz_next(ctx=Depends(get_ctx)):
    concept_id = next_concept(ctx)
    if concept_id is None:
        raise HTTPException(status_code=404, detail="no quiz concepts available")
    try:
        question = generate_question(ctx, concept_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"concept_id": concept_id, "question": question["question"]}


@quiz_router.post("/answer")
def quiz_answer(body: QuizAnswerRequest, ctx=Depends(get_ctx)):
    try:
        return grade_answer(ctx, body.concept_id, body.answer)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@quiz_router.get("/stats")
def quiz_stats(ctx=Depends(get_ctx)):
    return public_quiz_stats(ctx)
