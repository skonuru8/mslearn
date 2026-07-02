from dataclasses import dataclass

CONFLICT_CLASSIFICATIONS = (
    "context_dependent",
    "outdated",
    "genuine_debate",
    "evidence_mismatch",
)


def validate_classification(value: str) -> str:
    if value not in CONFLICT_CLASSIFICATIONS:
        raise ValueError(f"unknown conflict classification {value!r}")
    return value


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: str
    chunk_id: str
    source_id: str
    text: str
    stance: str  # "recommends" | "warns_against" | "neutral"
    quote: str
    trust: str   # "trusted" | "escalated" | "rejected"


@dataclass(frozen=True)
class ConceptRecord:
    concept_id: str
    name: str
    summary: str = ""
