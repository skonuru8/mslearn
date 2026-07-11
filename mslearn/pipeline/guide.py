from __future__ import annotations
from pydantic import BaseModel, ValidationError, field_validator
from mslearn.pipeline.contracts import CLAIM_KINDS
from mslearn.graph.records import CONFLICT_CLASSIFICATIONS

class GuideParseError(Exception): ...

class GuideItem(BaseModel):
    kind: str
    text: str
    claims: list[str] = []

class GuideSection(BaseModel):
    id: str
    title: str
    items: list[GuideItem] = []

class DisagreeSide(BaseModel):
    label: str
    text: str
    claims: list[str] = []

class Disagreement(BaseModel):
    summary: str
    classification: str
    a: DisagreeSide
    b: DisagreeSide

    @field_validator("classification")
    @classmethod
    def _classification_known(cls, value: str) -> str:
        if value not in CONFLICT_CLASSIFICATIONS:
            raise ValueError(f"unknown conflict classification {value!r}")
        return value

class TlDr(BaseModel):
    text: str
    claims: list[str] = []

INTERPRETATION_ANGLES = ("assumption", "evidence", "steelman", "verdict", "synthesis")

class InterpretationItem(BaseModel):
    angle: str
    text: str
    claims: list[str] = []

    @field_validator("angle")
    @classmethod
    def _angle_known(cls, value: str) -> str:
        if value not in INTERPRETATION_ANGLES:
            raise ValueError(f"unknown interpretation angle {value!r}")
        return value

class StudyGuide(BaseModel):
    concept_id: str
    title: str
    tl_dr: TlDr
    skeleton: list[str] = []
    sections: list[GuideSection] = []
    disagreements: list[Disagreement] = []
    open_questions: list[str] = []
    interpretation: list[InterpretationItem] = []

GUIDE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "concept_id": {"type": "string"},
        "title": {"type": "string"},
        "tl_dr": {"type": "object", "properties": {
            "text": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "claims"], "additionalProperties": False},
        "skeleton": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "title": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"enum": list(CLAIM_KINDS)}, "text": {"type": "string"},
                "claims": {"type": "array", "items": {"type": "string"}}},
                "required": ["kind", "text", "claims"], "additionalProperties": False}}},
            "required": ["id", "title", "items"], "additionalProperties": False}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "interpretation": {"type": "array", "items": {"type": "object", "properties": {
            "angle": {"enum": list(INTERPRETATION_ANGLES)}, "text": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "string"}}},
            "required": ["angle", "text", "claims"], "additionalProperties": False}},
    },
    "required": ["concept_id", "title", "tl_dr", "skeleton", "sections", "open_questions"],
    "additionalProperties": False,
}

def parse_guide(obj: object) -> StudyGuide:
    try:
        return StudyGuide.model_validate(obj)
    except ValidationError as exc:
        raise GuideParseError(str(exc)[:500]) from exc

def drop_ungrounded(guide: StudyGuide) -> StudyGuide:
    sections = []
    for s in guide.sections:
        kept = []
        for i in s.items:
            if any(c and c.strip() for c in i.claims):
                i.claims = [c for c in i.claims if c and c.strip()]
                kept.append(i)
        if kept:
            sections.append(GuideSection(id=s.id, title=s.title, items=kept))
    guide.sections = sections
    guide.skeleton = [t for t in guide.skeleton if any(s.title == t for s in sections)] or [s.title for s in sections]
    if not guide.tl_dr.claims:
        first_cited = next((i for s in sections for i in s.items if i.claims), None)
        if first_cited is not None:
            guide.tl_dr.claims = first_cited.claims
        else:
            guide.tl_dr.text = ""
    # interpretation items are labeled model analysis, not source facts —
    # they are kept regardless of whether they carry claim citations.
    return guide

# guide_gen.py still imports drop_uncited; keep this alias so it keeps
# working until that call site is switched over to drop_ungrounded.
drop_uncited = drop_ungrounded
