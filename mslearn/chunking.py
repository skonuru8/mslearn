import re
from dataclasses import dataclass

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit

CHUNK_TARGET_TOKENS = 500


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    chunk_id: str
    source_id: str
    unit_index: int
    seq: int
    text: str
    locator: Locator
    section_path: tuple[str, ...] = ()


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _split_oversize(paragraph: str) -> list[str]:
    parts: list[str] = []
    buf = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        candidate = f"{buf} {sentence}".strip() if buf else sentence
        if buf and estimate_tokens(candidate) > CHUNK_TARGET_TOKENS:
            parts.append(buf)
            buf = sentence
        else:
            buf = candidate
    if buf:
        parts.append(buf)

    out: list[str] = []
    window = CHUNK_TARGET_TOKENS * 4
    for part in parts:
        while estimate_tokens(part) > CHUNK_TARGET_TOKENS:
            out.append(part[:window])
            part = part[window:]
        if part:
            out.append(part)
    return out


def chunk_source(doc: SourceDocument) -> list[Chunk]:
    chunks: list[Chunk] = []

    def emit(unit: StructuralUnit, buf: list[str]) -> None:
        seq = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.source_id}:{seq}", source_id=doc.source_id,
                unit_index=unit.index, seq=seq,
                text="\n\n".join(buf), locator=unit.locator,
                section_path=unit.section_path,
            )
        )

    for unit in doc.units:
        pieces: list[str] = []
        for para in _paragraphs(unit.text):
            if estimate_tokens(para) > CHUNK_TARGET_TOKENS:
                pieces.extend(_split_oversize(para))
            else:
                pieces.append(para)

        buf: list[str] = []
        for piece in pieces:
            if buf and estimate_tokens("\n\n".join([*buf, piece])) > CHUNK_TARGET_TOKENS:
                emit(unit, buf)
                buf = []
            buf.append(piece)
        if buf:
            emit(unit, buf)
    return chunks
