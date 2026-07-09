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
from mslearn.providers.base import (
    ModelMessage,
    ModelRequest,
    ProviderBadOutputError,
    ProviderError,
    ProviderTransientError,
)


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
    parse_error: bool


def build_extraction_graph(router, db: OpsDB):
    max_attempts = int(db.get_tunable("extract.max_attempts"))
    max_tokens = int(db.get_tunable("extract.max_tokens"))
    max_claims = int(db.get_tunable("extract.max_claims"))
    quote_threshold = db.get_tunable("trust.quote_threshold")
    embed_threshold = db.get_tunable("trust.embed_sim_threshold")
    base_prompt = get_prompt(db, "extraction").format(max_claims=max_claims)
    retry_suffix = get_prompt(db, "extraction_retry_suffix")
    # Escalating re-runs extraction under the "synthesis" role. When that
    # role resolves to the SAME provider+model as "extraction" (e.g. the
    # openrouter profile: both deepseek-v4-flash), escalation is a no-op
    # that doubles calls/tokens for zero model change — skip the escalate
    # edge entirely in that case. Computed once at build time (not per
    # chunk): a profile switch is picked up on the next worker-process
    # restart, same as every other tunable read here.
    escalation_useful = not router.resolves_same("extraction", "synthesis")

    def extract(state: ExtractionState) -> dict:
        prompt = f"{base_prompt}\n\nCHUNK:\n{state['chunk_text']}"
        if state["reasons"]:
            prompt += retry_suffix.format(reasons="; ".join(state["reasons"][-4:]))
        role = "synthesis" if state["escalated"] else "extraction"
        request = ModelRequest(
            messages=[ModelMessage(role="user", content=prompt)],
            json_schema=EXTRACTION_SCHEMA,
            max_tokens=max_tokens,
        )
        try:
            response = router.complete(role, request)
            drafts = parse_extraction(response.parsed)
        except ProviderTransientError:
            raise
        except ProviderBadOutputError as exc:
            # A truncated/malformed model response (e.g. finish_reason=length
            # cutting off mid-JSON) is a parse-shaped failure, not a terminal
            # provider error — treat it like ExtractionParseError so one bad
            # generation gets a retry within the existing attempt budget
            # instead of failing the chunk outright.
            return {"drafts": [], "reasons": state["reasons"] + [f"bad output: {exc}"],
                    "attempt": state["attempt"] + 1, "parse_error": True}
        except ProviderError as exc:
            return {"error": str(exc)[:500], "drafts": []}
        except ExtractionParseError as exc:
            return {"drafts": [], "reasons": state["reasons"] + [f"parse: {exc}"],
                    "attempt": state["attempt"] + 1, "parse_error": True}
        return {"drafts": drafts, "attempt": state["attempt"] + 1, "parse_error": False}

    def validate(state: ExtractionState) -> dict:
        if state["error"] is not None:
            return {}
        if state["parse_error"]:
            return {
                "rejected": [{"draft": None, "reasons": state["reasons"][-1:]}],
                "reasons": state["reasons"],
            }
        accepted = list(state["accepted"])
        seen = {d.text for d in accepted}

        # Batch every trust-check embed for this validate() pass into ONE
        # router.embed call instead of one call per draft — check_claim used
        # to call embedder([draft.text, quote]) itself, every single draft,
        # which at chunk volume was one network round trip per draft. Every
        # draft's own text plus every distinct non-empty quote among them go
        # into a single combined request; check_claim then gets its draft's
        # text vector directly and looks its quote vector up from a small
        # in-memory cache seeded by that same call (see quote_embedder
        # below) — zero extra network calls, identical cosine math.
        texts = [d.text for d in state["drafts"]]
        quotes = sorted({d.quote.strip() for d in state["drafts"] if d.quote.strip()})
        embeddings = router.embed(texts + quotes) if (texts or quotes) else []
        claim_embeddings = dict(zip(texts, embeddings[: len(texts)]))
        quote_vectors = dict(zip(quotes, embeddings[len(texts):]))

        def quote_embedder(qs: list[str]) -> list[list[float]]:
            return [quote_vectors[q] for q in qs]

        failing: list[dict] = []
        reasons: list[str] = []
        for draft in state["drafts"]:
            if draft.text in seen:
                continue
            verdict = check_claim(
                state["chunk_text"], draft,
                quote_threshold=quote_threshold,
                embed_sim_threshold=embed_threshold,
                embedder=quote_embedder,
                claim_embedding=claim_embeddings.get(draft.text),
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
        if not state["escalated"] and escalation_useful:
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


def run_extraction(graph, chunk_id: str, chunk_text: str) -> ExtractionState:
    """Run extraction on a chunk through a graph built once per worker
    process (see worker/context.py's `extraction_graph`) — recompiling the
    StateGraph and re-reading every tunable on each chunk was pure per-chunk
    overhead the graph structure and tunables don't need."""
    initial: ExtractionState = {
        "chunk_id": chunk_id, "chunk_text": chunk_text, "attempt": 0,
        "escalated": False, "drafts": [], "accepted": [], "rejected": [],
        "reasons": [], "error": None, "parse_error": False,
    }
    return graph.invoke(initial)
