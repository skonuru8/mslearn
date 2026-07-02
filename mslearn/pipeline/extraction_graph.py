from typing import TypedDict

from langgraph.graph import END, StateGraph

from mslearn.opsdb import OpsDB
from mslearn.pipeline.contracts import (
    EXTRACTION_SCHEMA,
    ClaimDraft,
    ExtractionParseError,
    parse_extraction,
)
from mslearn.pipeline.trust import check_claim
from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderError


class ExtractionState(TypedDict):
    chunk_id: str
    chunk_text: str
    attempt: int
    escalated: bool
    drafts: list[ClaimDraft]
    accepted: list[ClaimDraft]
    rejected: list[dict]
    reasons: list[str]
    error: str | None


def build_extraction_graph(router, db: OpsDB):
    max_attempts = int(db.get_tunable("extract.max_attempts"))
    quote_threshold = db.get_tunable("trust.quote_threshold")
    embed_threshold = db.get_tunable("trust.embed_sim_threshold")
    base_prompt = get_prompt(db, "extraction")
    retry_suffix = get_prompt(db, "extraction_retry_suffix")

    def extract(state: ExtractionState) -> dict:
        prompt = f"{base_prompt}\n\nCHUNK:\n{state['chunk_text']}"
        if state["reasons"]:
            prompt += retry_suffix.format(reasons="; ".join(state["reasons"][-4:]))
        role = "synthesis" if state["escalated"] else "extraction"
        request = ModelRequest(
            messages=[ModelMessage(role="user", content=prompt)],
            json_schema=EXTRACTION_SCHEMA,
        )
        try:
            response = router.complete(role, request)
            drafts = parse_extraction(response.parsed)
        except ProviderError as exc:
            return {"error": str(exc)[:500], "drafts": []}
        except ExtractionParseError as exc:
            return {"drafts": [], "reasons": state["reasons"] + [f"parse: {exc}"],
                    "attempt": state["attempt"] + 1}
        return {"drafts": drafts, "attempt": state["attempt"] + 1}

    def validate(state: ExtractionState) -> dict:
        if state["error"] is not None:
            return {}
        accepted = list(state["accepted"])
        seen = {d.text for d in accepted}
        failing: list[dict] = []
        reasons: list[str] = []
        for draft in state["drafts"]:
            if draft.text in seen:
                continue
            verdict = check_claim(
                state["chunk_text"], draft,
                quote_threshold=quote_threshold,
                embed_sim_threshold=embed_threshold,
                embedder=router.embed,
            )
            if verdict.ok:
                accepted.append(draft)
                seen.add(draft.text)
            else:
                failing.append({"draft": draft.model_dump(), "reasons": verdict.reasons})
                reasons.extend(verdict.reasons)
        return {"accepted": accepted, "rejected": failing, "reasons": reasons}

    def route(state: ExtractionState) -> str:
        if state["error"] is not None or not state["rejected"]:
            return "done"
        if state["attempt"] < max_attempts:
            return "retry"
        if not state["escalated"]:
            return "escalate"
        return "done"

    def escalate(state: ExtractionState) -> dict:
        return {"escalated": True, "attempt": 0}

    builder = StateGraph(ExtractionState)
    builder.add_node("extract", extract)
    builder.add_node("validate", validate)
    builder.add_node("escalate", escalate)
    builder.set_entry_point("extract")
    builder.add_edge("extract", "validate")
    builder.add_conditional_edges(
        "validate", route, {"retry": "extract", "escalate": "escalate", "done": END}
    )
    builder.add_edge("escalate", "extract")
    return builder.compile()


def run_extraction(router, db: OpsDB, chunk_id: str, chunk_text: str) -> ExtractionState:
    graph = build_extraction_graph(router, db)
    initial: ExtractionState = {
        "chunk_id": chunk_id, "chunk_text": chunk_text, "attempt": 0,
        "escalated": False, "drafts": [], "accepted": [], "rejected": [],
        "reasons": [], "error": None,
    }
    return graph.invoke(initial)
