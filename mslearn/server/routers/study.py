from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mslearn.pipeline.guide_gen import generate_guide
from mslearn.pipeline.quiz import generate_question, grade_answer, next_concept, public_quiz_stats
from mslearn.pipeline.study_extras import make_flashcards, make_selfcheck
from mslearn.pipeline.study_outline import build_outline
from mslearn.server.deps import get_ctx, get_project_id
from mslearn.worker.tasks import synthesize_task

router = APIRouter(prefix="/api/study", tags=["study"])
quiz_router = APIRouter(prefix="/api/quiz", tags=["quiz"])

ALLOWED_FEEDBACK_TAGS = {"too_shallow", "repetitive", "wrong", "off_topic"}


class FlagRequest(BaseModel):
    reason: str


class FeedbackRequest(BaseModel):
    helpful: bool | None = None
    tags: list[str] = Field(default_factory=list)
    comment: str = ""
    guide_hash: str | None = None


class ProgressRequest(BaseModel):
    section_id: str
    reviewed: bool


class CountRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=50)


class QuizAnswerRequest(BaseModel):
    concept_id: str
    answer: str
    session_id: str


@router.get("/curriculum")
def curriculum(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return ctx.graph.curriculum(project_id=project_id)


@router.get("/outline")
def outline(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return build_outline(ctx.graph.curriculum(project_id=project_id))


@router.get("/concepts/{concept_id}")
def concept(concept_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    concept_row = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept_row is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    claims = [
        c
        for c in ctx.graph.claims_in_concept(concept_id, project_id=project_id)
        if c.get("trust", "trusted") in {"trusted", "escalated", "image_observed"}
    ]
    return {
        "concept": concept_row,
        "claims": claims,
        "conflicts": ctx.graph.conflicts_in_concept(concept_id, project_id=project_id),
        "citations": ctx.graph.citations_for_claims(
            [row["claim_id"] for row in claims], project_id=project_id
        ),
    }


@router.get("/concepts/{concept_id}/teach")
def teach(
    concept_id: str,
    force: bool = False,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    try:
        guide, cached = generate_guide(ctx, concept_id, force=force, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {
        "guide": guide,
        "cached": cached,
        "progress": ctx.db.section_progress(project_id, concept_id),
    }


@router.post("/concepts/{concept_id}/progress")
def set_progress(
    concept_id: str,
    body: ProgressRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    if ctx.graph.get_concept(concept_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    ctx.db.set_section_reviewed(project_id, concept_id, body.section_id, body.reviewed)
    return {"progress": ctx.db.section_progress(project_id, concept_id)}


@router.post("/concepts/{concept_id}/flashcards")
def flashcards(
    concept_id: str,
    body: CountRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    if ctx.graph.get_concept(concept_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    try:
        cards = make_flashcards(ctx, concept_id, body.count, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"cards": cards}


@router.post("/concepts/{concept_id}/selfcheck")
def selfcheck(
    concept_id: str,
    body: CountRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    if ctx.graph.get_concept(concept_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    try:
        checks = make_selfcheck(ctx, concept_id, body.count, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"checks": checks}


@router.post("/claims/{claim_id}/flag")
def flag_claim(
    claim_id: str,
    body: FlagRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    concept_id = ctx.graph.concept_id_of_claim(claim_id, project_id=project_id)
    if concept_id is None:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id!r}")
    ctx.graph.set_claim_trust(claim_id, "rejected", project_id=project_id)
    ctx.graph.mark_concept_dirty(concept_id, True, project_id=project_id)
    ctx.graph.set_concept_teaching(concept_id, "", project_id=project_id)
    synthesize_task.delay(project_id)
    return {"claim_id": claim_id, "concept_id": concept_id, "status": "flagged"}


@router.post("/concepts/{concept_id}/feedback")
def submit_feedback(
    concept_id: str,
    body: FeedbackRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    bad = [t for t in body.tags if t not in ALLOWED_FEEDBACK_TAGS]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown tags {bad}")
    ctx.db.add_note_feedback(
        project_id,
        concept_id,
        helpful=body.helpful,
        tags=body.tags,
        comment=body.comment,
        guide_hash=body.guide_hash,
    )
    return {"ok": True}


@router.get("/concepts/{concept_id}/feedback")
def get_feedback(concept_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return ctx.db.feedback_for_concept(concept_id, project_id) or {}


@quiz_router.get("/next")
def quiz_next(session_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    concept_id = next_concept(ctx, project_id)
    if concept_id is None:
        raise HTTPException(status_code=404, detail="no quiz concepts available")
    try:
        question = generate_question(ctx, concept_id, session_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"concept_id": concept_id, "question": question["question"]}


@quiz_router.post("/answer")
def quiz_answer(
    body: QuizAnswerRequest, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)
):
    try:
        return grade_answer(ctx, body.concept_id, body.answer, body.session_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@quiz_router.get("/stats")
def quiz_stats(ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return public_quiz_stats(ctx, project_id)
