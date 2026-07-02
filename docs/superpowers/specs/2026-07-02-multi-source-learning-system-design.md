# Personal Multi-Source Learning System — Final Design (Spec)

## Context

The user drafted a 7-layer architecture for a personal tool that turns mixed sources (books, blogs, YouTube, audio) into structured understanding: extraction into a concept graph, explicit cross-source conflict classification, and teaching/quiz output grounded in citations. They asked for a critique, then ran a brainstorming session to converge on a **single final build — no MVP iterations — production-grade, with a full eval suite as the definition of done**.

Fact-checks performed during review (all of the doc's flagged claims verified):
- Claude Code `--bare` exists and is API-key-only (OAuth/keychain never read); non-bare `claude -p` uses subscription auth; `--resume`/`--fork-session` exist.
- `qwen3.5:9b` exists on Ollama (9.65B params, ~6.6GB Q4_K_M, 256K context, native structured output).

Critique conclusions incorporated:
- Grounding via embedding-similarity alone is noisy → replaced with verbatim-quote matching + embedding sanity check.
- Entity resolution (clustering) is the hardest problem → gets its own golden set and eval gate.
- Cut as over-engineering: ensemble/majority-vote extraction; the few-shot self-recalibration loop (original §9.5).

## Decisions from brainstorming (user-confirmed)

| Decision | Choice |
|---|---|
| Scope | Final build in one effort; everything below ships |
| Latency | Interactive (Q&A/teach/quiz/corrections) = seconds, streaming; ingestion = background pipeline |
| Interface | Local web app (localhost) + Markdown/Anki file export |
| Sources | Books (PDF/EPUB), blogs/web, YouTube, audio/podcasts — all four |
| Evals | Full: component golden sets + deterministic metrics + judged end-to-end, numeric release gates |
| Backends | Local Qwen 3.5 9B (Ollama) for extraction; DeepSeek via OpenRouter for judgment/interactive (chat-class interactive, R1-class synthesis); Claude Code (non-bare `claude -p`) as switchable alternative |
| Switching | Named profiles in `profiles.yaml` (`openrouter` default, `claude-code`, `offline`) + hot-swap UI toggle |
| Stack | Python/FastAPI backend + typed React/Vite frontend |
| Process shape | Celery + Redis task queue (Redis broker-only, never a datastore) |
| Knowledge graph | **Neo4j Community** (user choice) — property graph + native vector indexes; Neo4j Browser for visualization; portability preserved via GraphML/JSON dumps on every export |
| Orchestration | **Celery + LangGraph** (user choice): Celery = parallelism across chunks/sources; LangGraph = control flow inside each unit (extract→validate→escalate; synthesis; Q&A/teaching graphs) with checkpointing + streaming |
| Memory | **mem0 graph memory on the same Neo4j** (user choice), scoped to learner/interaction memory only — see integrity rule in §3b |
| Operational store | SQLite (WAL): model-call logs, eval golden sets + results, quiz records |

Load target (validated): ~140 sources (15 books + 25 videos + 100 blogs) ≈ 12–15k extraction chunks. Local-only ≈ 1–3 days background; OpenRouter burst ≈ 1–2 hours for a few dollars. Profile choice at enqueue time.

## Architecture

### 1. System shape
Monorepo: `server/` (FastAPI — UI serving, Q&A/teaching endpoints, graph reads), `worker/` (Celery tasks wrapping LangGraph runs), `frontend/` (React/Vite), `graphs/` (LangGraph definitions), `core/` (providers, profiles, schemas). `docker-compose` provides **Redis** (broker) and **Neo4j Community** (knowledge graph + vectors + mem0 backend); `make run` boots services + API + workers (Procfile-style). Three Celery queues: `ingest` (local model, concurrency 2 = `OLLAMA_NUM_PARALLEL`), `judge` (OpenRouter, concurrency ~8, rate-limited), `transcribe` (Whisper, concurrency 1, exclusive with heavy ingest — 18GB memory ceiling).

### 2. Ingestion pipeline
Adapters → normalized `SourceDocument {source_id, role, structural_units, text, locators(page/timestamp/url-anchor)}`:
- Books: PyMuPDF (PDF) / ebooklib (EPUB) · Blogs: trafilatura · YouTube: youtube-transcript-api, caption-less fallback yt-dlp→Whisper · Audio: faster-whisper

Structure-aware chunking (~200–500 tokens, locators preserved). Each chunk runs a **LangGraph extraction graph**: extract (schema-enforced JSON via Ollama structured outputs) → trust gate → conditional retry (span re-emphasized) → conditional escalate (`judge` queue) → commit. Claim schema includes a **verbatim quote of the supporting span**. Trust gate: (a) schema check, (b) quote fuzzy-string-matches the chunk, (c) claim↔span embedding similarity sanity check. Per-source failure-rate monitor pauses the queue (environmental signal) instead of retrying harder. Chunks commit atomically post-gate; the queue is persistent and resumable (sleep/crash safe, no rework). Scheduling: spine first, then supplements smallest-first; sources usable as they finish.

### 3. Concept graph & synthesis (Neo4j)
Property graph — node labels: `Source`, `Chunk`, `Claim` (trust status, stance, verbatim quote, locator), `Concept` (cluster). Relationships: `EXTRACTED_FROM`, `IN_CONCEPT`, `DEPENDS_ON`, `CONFLICTS_WITH {classification, rationale}`. Embeddings in Neo4j native vector indexes (claims + chunks). Traversals in Cypher; Neo4j Browser available for ad-hoc graph exploration.

Synthesis runs as a checkpointed **LangGraph synthesis graph** on the judge backend: vector-index candidate pairs → same-concept verdicts → clusters; intra-cluster disagreement → tension classification {context-dependent, outdated, genuine debate, evidence mismatch}, steered by per-corpus domain profile (technical → resolve with context; interpretive → preserve framings). Curriculum = spine structure + `DEPENDS_ON` topological sort; supplements attach by cluster. Spine explicitly declared at corpus setup; if omitted, system infers and asks for confirmation in the UI. Incremental: new claims/corrections mark only touched concepts dirty; only those re-synthesize.

**Portability rule:** every export run also dumps the full knowledge graph to GraphML + JSON files alongside the Markdown/Anki output, so no knowledge is locked inside the Neo4j server.

### 3b. Memory layer (mem0 on Neo4j) — integrity rule
mem0 graph memory (same Neo4j instance, separate label namespace) stores **learner/interaction memory only**: questions asked, concepts that caused confusion, quiz-failure patterns, stated preferences. Teaching, quiz selection, and Q&A graphs read memory to personalize delivery (e.g., revisit weak concepts, adjust depth). **Memory never asserts source knowledge**: claims enter the concept graph only through the trust gate, memory is never consulted for facts, and if memory and graph disagree the graph wins by construction. Memory writes happen from interactive-session LangGraph nodes; all memory is inspectable and deletable from a UI panel.

### 4. Interactive layer & outputs
Web app views: **Corpus manager** (add sources, spine assignment, progress, pause/burst), **Curriculum browser**, **Teaching view** (explanation + worked example + misconception + *required* side-by-side tension callouts with span-linked citations), **Quiz** (reasoning questions; free-text answers graded with explanation by judge backend; results recorded and fed to memory), **Q&A chat** (streaming LangGraph: retrieve (vector + graph) → optionally consult memory for personalization → answer; framework-attributed answers when clusters conflict), plus a **Memory panel** (inspect/delete what the system has learned about you). Every displayed claim has a flag control → dirty-propagation regeneration; manual edits equally valid. Exports: deterministic Markdown notes per concept + Anki `.apkg` + GraphML/JSON graph dump, regenerable without model calls.

### 5. Model backend layer
`ModelProvider` interface (`complete(request) → structured response`), three implementations: Ollama, OpenRouter (OpenAI-compatible), Claude Code (non-bare `claude -p` subprocess). LangGraph nodes call providers through this interface only. `profiles.yaml` maps roles {extraction, synthesis, interactive, evals} → provider+model+params per named profile. UI toggle hot-swaps profile (effective next call). Model IDs live in config, never code. Every call logged to SQLite (role, provider, tokens, latency, cost, outcome) → spend/latency panel in UI.

### 6. Evals — definition of done
Golden sets (seeded by strong model, corrected by user in a review view): extraction (~200 chunks across all 4 source types), grounding calibration, clustering pairs (~300), tension classification (~100). Deterministic metrics (always on): schema validity, quote-match rate, chunk coverage, pipeline success rate. Judged evals: rubric-scored note/teaching/quiz quality on the strong backend. **Release gates:** extraction precision ≥ 0.90 / recall ≥ 0.85; grounding false-accept ≤ 2%; clustering F1 ≥ 0.80; tension accuracy ≥ 0.75; schema validity ≥ 0.99. Additional gate for the memory layer: personalization is advisory-only (an eval asserts no fact in any generated output originates from memory — every factual sentence must trace to a trust-gated claim). Eval runner = Celery tasks + UI report page + pytest entry point. Open thresholds (grounding cutoff, retry counts) tuned against golden sets, not guessed.

**Self-evolution (added 2026-07-02, user requirement):** the app reads its own eval results and adapts. Mechanism: every tunable parameter (trust-gate thresholds, retry counts, failure-rate cutoffs) and every pipeline prompt is resolved at call time from an audited `tunables`/prompt-override store in SQLite — never hardcoded. The eval loop (Plan 8) closes the circle: run evals → judge model proposes adjusted thresholds/prompt variants → a candidate change is accepted only if it improves golden-set scores (gated, never blind) → written to the tunables store with a full audit trail (when/why/old/new). The pipeline picks up new values on the next call; a UI panel shows tunable history with rollback. This supersedes the earlier cut of §9.5 self-recalibration — it returns in eval-gated form.

### 7. Error handling & testing
Taxonomy: transient (network/rate-limit → Celery retry/backoff), model-quality (trust-gate fail → LangGraph retry edge → escalate edge), environmental (failure-rate → pause queue + UI banner), fatal-per-source (unparseable input → source marked failed with reason; never blocks corpus). LangGraph checkpoints make interactive sessions resumable; pipeline graphs are idempotent per chunk. Testing: adapter unit tests on fixture files; chunker property tests (no text loss, valid locators); LangGraph graphs unit-tested with fake providers; VCR-style integration tests (recorded model responses; CI needs no live model); e2e smoke on a tiny bundled corpus; eval suite as the final gate.

## Explicitly excluded (agreed)
- GraphRAG frameworks (generic extraction would bypass the trust gate and replace the differentiators)
- Ensemble/majority-vote extraction; few-shot self-recalibration from manual overrides
- Redis as a datastore (broker only)

## Critical files to create (implementation plan will detail)
`docker-compose.yml` (Redis, Neo4j); `server/` FastAPI app + routers; `worker/` Celery app + queue config; `graphs/` LangGraph definitions (extraction, synthesis, qa, teaching, quiz); `adapters/` (pdf, epub, blog, youtube, audio); `core/` (Neo4j schema/constraints + Cypher layer, providers, profiles, mem0 setup); `frontend/` React app (6 views incl. memory panel); `evals/` golden sets + runners; `Procfile`/`Makefile`; `profiles.yaml`.

## Verification (end-to-end)
1. `make run` → Redis + Neo4j + API + workers up; web UI on localhost; Neo4j Browser reachable.
2. Ingest bundled smoke corpus (1 tiny PDF + 1 blog + 1 captioned video) on `offline` profile → claims appear with valid citations; curriculum renders; conflict callout appears on the planted disagreement; Markdown + Anki + GraphML/JSON exports open cleanly.
3. Switch profile to `openrouter` in UI → Q&A streams in seconds; quiz grading returns explanations; a deliberately failed quiz question shows up in the Memory panel and biases the next review suggestion.
4. `pytest` → unit/property/graph/integration suites green without network.
5. Eval run from UI (and `pytest -m evals`) → all release gates meet thresholds, including the memory-provenance gate; report page shows per-component scores.
6. Resilience: kill the worker mid-ingestion, restart → resumes without duplicate claims (idempotency check in Neo4j).

## Next step after approval
Save this spec to `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md`, `git init` + commit, then invoke `superpowers:writing-plans` to produce the phased implementation plan.
