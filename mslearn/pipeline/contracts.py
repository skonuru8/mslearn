import hashlib

from pydantic import BaseModel, ValidationError, field_validator

from mslearn.graph.records import ClaimRecord

STANCES = ("recommends", "warns_against", "neutral")

CLAIM_KINDS = ("definition", "claim", "mechanism", "example", "caveat", "actionable")

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "stance": {"enum": list(STANCES)},
                    "quote": {"type": "string"},
                    "kind": {"enum": list(CLAIM_KINDS)},
                },
                "required": ["text", "stance", "quote", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


class ExtractionParseError(Exception):
    """Model output did not match the extraction contract."""


class ClaimDraft(BaseModel):
    text: str
    stance: str
    quote: str
    kind: str = "claim"

    @field_validator("stance")
    @classmethod
    def _stance_known(cls, value: str) -> str:
        if value not in STANCES:
            raise ValueError(f"unknown stance {value!r}")
        return value

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, value: str) -> str:
        if value not in CLAIM_KINDS:
            raise ValueError(f"unknown kind {value!r}")
        return value


class _ExtractionOutput(BaseModel):
    claims: list[ClaimDraft]


def parse_extraction(parsed: object) -> list[ClaimDraft]:
    try:
        return _ExtractionOutput.model_validate(parsed).claims
    except ValidationError as exc:
        raise ExtractionParseError(str(exc)[:500]) from exc


def derive_claim_id(chunk_id: str, text: str) -> str:
    return hashlib.sha256(f"{chunk_id}:{text}".encode()).hexdigest()[:16]


def to_claim_record(draft: ClaimDraft, *, chunk_id: str, source_id: str,
                    trust: str) -> ClaimRecord:
    return ClaimRecord(
        claim_id=derive_claim_id(chunk_id, draft.text),
        chunk_id=chunk_id, source_id=source_id,
        text=draft.text, stance=draft.stance, quote=draft.quote, trust=trust,
        kind=draft.kind,
    )
