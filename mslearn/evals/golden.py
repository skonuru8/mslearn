from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from mslearn.graph.records import validate_classification

ReviewStatus = Literal["pending", "approved", "corrected"]
GOLDEN_KINDS = ("extraction", "grounding", "clustering", "tension", "guide")
GOLDEN_DIR = Path(__file__).resolve().parents[2] / "evals" / "golden"
ACTIVE_REVIEWS = frozenset({"approved", "corrected"})


class GoldenFormatError(ValueError):
    pass


@dataclass
class ExtractionGolden:
    chunk_text: str
    expected_claims: list[dict]
    source_type: str
    review: ReviewStatus = "approved"

    def __post_init__(self) -> None:
        for claim in self.expected_claims:
            if not isinstance(claim, dict) or "text" not in claim or "stance" not in claim:
                raise GoldenFormatError("expected_claims entries need text and stance")


@dataclass
class GroundingGolden:
    chunk_text: str
    claim_text: str
    quote: str
    valid: bool
    review: ReviewStatus = "approved"


@dataclass
class ClusteringGolden:
    text_a: str
    text_b: str
    same_concept: bool
    review: ReviewStatus = "approved"


@dataclass
class TensionGolden:
    claim_a: str
    claim_b: str
    domain_profile: str
    classification: str
    review: ReviewStatus = "approved"

    def __post_init__(self) -> None:
        if self.domain_profile not in {"technical", "interpretive"}:
            raise GoldenFormatError(f"invalid domain_profile {self.domain_profile!r}")
        validate_classification(self.classification)


GUIDE_AXES = ("depth", "non_redundancy", "category_fit", "grounding")


@dataclass
class GuideGolden:
    """A regression fixture ratcheting a flagged note into the guide judge:
    the concept's claims frozen at promotion time (so the fixture stays
    stable even as the live graph changes), the axis the user's feedback
    tag maps to, and the feedback tag itself for context."""

    concept_id: str
    concept_name: str
    concept_summary: str
    claims: list[dict]
    failing_axis: str
    tag: str
    review: ReviewStatus = "approved"

    def __post_init__(self) -> None:
        if self.failing_axis not in GUIDE_AXES:
            raise GoldenFormatError(f"invalid failing_axis {self.failing_axis!r}")
        for claim in self.claims:
            if not isinstance(claim, dict) or "claim_id" not in claim or "text" not in claim:
                raise GoldenFormatError("claims entries need claim_id and text")


def _golden_path(kind: str) -> Path:
    if kind not in GOLDEN_KINDS:
        raise KeyError(f"unknown golden kind {kind!r}")
    return GOLDEN_DIR / f"{kind}.jsonl"


def load_golden(kind: str, *, active_only: bool = False) -> list:
    path = _golden_path(kind)
    if not path.exists():
        return []
    cls = _class_for_kind(kind)
    rows = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenFormatError(f"{path.name}:{line_no}: invalid JSON") from exc
        try:
            row = cls(**payload)
        except (TypeError, GoldenFormatError, ValueError) as exc:
            raise GoldenFormatError(f"{path.name}:{line_no}: {exc}") from exc
        if active_only and row.review not in ACTIVE_REVIEWS:
            continue
        rows.append(row)
    return rows


def save_golden(kind: str, records: list) -> None:
    path = _golden_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    cls = _class_for_kind(kind)
    lines = []
    for index, record in enumerate(records):
        if not isinstance(record, cls):
            raise GoldenFormatError(f"record {index} is not {cls.__name__}")
        lines.append(json.dumps(asdict(record), ensure_ascii=False))
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def append_golden(kind: str, record) -> None:
    records = load_golden(kind)
    records.append(record)
    save_golden(kind, records)


def _class_for_kind(kind: str):
    return {
        "extraction": ExtractionGolden,
        "grounding": GroundingGolden,
        "clustering": ClusteringGolden,
        "tension": TensionGolden,
        "guide": GuideGolden,
    }[kind]


def replace_golden_record(kind: str, index: int, record) -> None:
    records = load_golden(kind)
    if index < 0 or index >= len(records):
        raise IndexError(f"golden index out of range: {index}")
    records[index] = record
    save_golden(kind, records)


def delete_golden_record(kind: str, index: int) -> None:
    records = load_golden(kind)
    if index < 0 or index >= len(records):
        raise IndexError(f"golden index out of range: {index}")
    del records[index]
    save_golden(kind, records)
