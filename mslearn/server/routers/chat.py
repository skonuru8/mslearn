from __future__ import annotations

import json
import re
from collections import OrderedDict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mslearn.pipeline.qa import retrieve
from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderError
from mslearn.server.deps import get_ctx, get_project_id

router = APIRouter(prefix="/api/chat", tags=["chat"])

_CLAIM_RE = re.compile(r"\[claim:([^\]\s]+)\]")
# Process-local, in-memory, LRU-capped: fine for a single uvicorn worker
# (this deployment's assumption); breaks session continuity across
# `uvicorn --workers N` or multiple app processes. Move to OpsDB if that
# ever changes.
_SESSIONS: "OrderedDict[str, list[dict[str, str]]]" = OrderedDict()
_MAX_TURNS = 10
_MAX_SESSIONS = 500


class ChatRequest(BaseModel):
    question: str
    session_id: str


@router.post("")
def chat(
    body: ChatRequest,
    ctx=Depends(get_ctx),
    project_id: str = Depends(get_project_id),
):
    session_key = f"{project_id}:{body.session_id}"
    retrieval = retrieve(ctx, body.question, project_id=project_id)
    request = ModelRequest(
        messages=_messages(ctx, session_key, body.question, retrieval, project_id),
    )

    def events():
        answer_parts: list[str] = []
        try:
            for delta in ctx.router.stream("interactive", request):
                text = str(delta)
                answer_parts.append(text)
                yield _sse({"delta": text})
        except ProviderError as exc:
            yield _sse({"error": str(exc)[:300]})
            return
        answer = "".join(answer_parts)
        citations = _claim_ids(answer)
        yield _sse({"done": True, "citations": citations})
        _append_turn(session_key, body.question, answer)
        _record_interaction(ctx.memory, body.question, project_id)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/sessions/{session_id}")
def session(session_id: str, project_id: str = Depends(get_project_id)):
    session_key = f"{project_id}:{session_id}"
    return {"session_id": session_id, "turns": list(_SESSIONS.get(session_key, []))}


def _messages(
    ctx, session_id: str, question: str, retrieval: dict, project_id: str
) -> list[ModelMessage]:
    messages = [
        ModelMessage(
            role="system",
            content=get_prompt(ctx.db, "qa_answer").format(
                question=question,
                claims=_format_claims(retrieval["claims"]),
                chunks=_format_chunks(retrieval["chunks"]),
                conflicts=_format_conflicts(retrieval["conflicts"]),
                memory_hints=_format_memory_hints(ctx.memory, question, project_id),
            ),
        )
    ]
    if session_id in _SESSIONS:
        _SESSIONS.move_to_end(session_id)
    for turn in _SESSIONS.get(session_id, [])[-_MAX_TURNS:]:
        messages.append(ModelMessage(role="user", content=turn["question"]))
        messages.append(ModelMessage(role="assistant", content=turn["answer"]))
    messages.append(ModelMessage(role="user", content=question))
    return messages


def _format_claims(claims: list[dict]) -> str:
    if not claims:
        return "(none)"
    return "\n".join(
        f"- [claim:{row['claim_id']}] Source {row.get('source_id', '')} "
        f"({row.get('stance', '')}, trust={row.get('trust', '')}): {row.get('text', '')}"
        for row in claims
    )


def _format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "(none)"
    lines = []
    for row in chunks:
        locator = ", ".join(
            f"{key}={value}"
            for key, value in row.items()
            if key not in {"text", "score"} and value is not None
        )
        lines.append(f"- chunk {row.get('chunk_id', '')} ({locator}): {row.get('text', '')}")
    return "\n".join(lines)


def _format_conflicts(conflicts: list[dict]) -> str:
    if not conflicts:
        return "(none)"
    return "\n".join(
        "- [claim:{claim_a}] vs [claim:{claim_b}] ({classification}): {rationale}".format(
            claim_a=row.get("claim_a", ""),
            claim_b=row.get("claim_b", ""),
            classification=row.get("classification", ""),
            rationale=row.get("rationale", ""),
        )
        for row in conflicts
    )


def _format_memory_hints(memory, question: str, project_id: str) -> str:
    if memory is None:
        return "(none - PERSONALIZATION ONLY)"
    hits = memory.search(question, k=5, project_id=project_id)
    if not hits:
        return "(none - PERSONALIZATION ONLY)"
    return "\n".join(f"- PERSONALIZATION ONLY: {_memory_text(item)}" for item in hits)


def _memory_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))


def _claim_ids(answer: str) -> list[str]:
    seen = set()
    ids = []
    for claim_id in _CLAIM_RE.findall(answer):
        if claim_id not in seen:
            ids.append(claim_id)
            seen.add(claim_id)
    return ids


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _append_turn(session_id: str, question: str, answer: str) -> None:
    turns = _SESSIONS.setdefault(session_id, [])
    turns.append({"question": question, "answer": answer})
    del turns[:-_MAX_TURNS]
    _SESSIONS.move_to_end(session_id)
    while len(_SESSIONS) > _MAX_SESSIONS:
        _SESSIONS.popitem(last=False)  # evict least-recently-used session


def _record_interaction(memory, question: str, project_id: str) -> None:
    if memory is None:
        return
    memory.add(f"asked about: {question[:160]}", "interaction", project_id=project_id)
