# mslearn — Personal Multi-Source Learning System

Turns your books, blogs, YouTube playlists, and podcasts into a trust-gated
concept graph with cross-source conflict classification, teaching, quizzes,
and portable Markdown/Anki exports.

Spec: `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md`

## Prerequisites
- Python 3.12+, Docker, [Ollama](https://ollama.com) with models pulled:
  `ollama pull qwen3.5:9b && ollama pull nomic-embed-text`
- An OpenRouter API key (default profile) and/or Claude Code installed

## Setup
    python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
    cp .env.example .env   # fill in MSL_OPENROUTER_API_KEY
    make services          # starts Redis + Neo4j (browser: http://localhost:7474)

## Verify
    make check                                    # lint + offline test suite
    .venv/bin/python scripts/smoke_providers.py   # live local-provider smoke
    .venv/bin/python scripts/smoke_providers.py interactive   # + OpenRouter

## Backend profiles
Model routing lives in `profiles.yaml` (profiles: `openrouter` default,
`claude-code`, `offline`). Model IDs are config-only — edit the YAML to bump.

## Sources

`load_source(ref)` ingests any supported source into a normalized `SourceDocument`:
PDF/EPUB books (page/href citations), blog URLs or saved HTML (trafilatura),
YouTube videos (captions, whisper fallback), and audio files (faster-whisper).
`chunk_source(doc)` packs it into ≤500-token chunks with locators preserved.
Audio/caption-less-video ingestion downloads a Whisper model on first use.

## Concept graph

Neo4j holds the knowledge graph (browser: http://localhost:7474). `GraphStore`
owns all Cypher: schema/vector indexes, source/chunk/claim upserts, concepts
with `DEPENDS_ON` and classified `CONFLICTS_WITH` edges, vector search, and
portable GraphML/JSON export (embeddings excluded). Integration tests need
`make services`; they skip cleanly when Neo4j is down (`make graph-test`).

## Ingestion

`python -m mslearn.ingest_cli <ref> --role spine --local` ingests one source
inline (adapter → chunks → claim extraction → trust gate → Neo4j). Production
mode: `make services`, `make worker`, then enqueue without `--local`. Jobs are
durable in SQLite — `resume_pending()` re-enqueues after a crash; a source
whose chunks fail past the failure-rate tunable is paused, never retried
blindly. Thresholds and prompts are tunables (audited) — the eval loop adjusts
them; see the spec's self-evolution section.

## Synthesis

When a source finishes (all chunks `done` or `failed`), the worker marks it
`done` and enqueues `synthesize_task` on the `judge` queue. Synthesis runs in
three incremental phases: cluster unassigned trusted claims into concepts,
process dirty concepts for conflict classification + naming, then rebuild the
curriculum order.

Domain profile steers conflict classification:
- `corpus.domain_profile=technical` (default): prefer `context_dependent`
- `corpus.domain_profile=interpretive`: prefer `genuine_debate`

Core tunables:
- `synth.candidate_k` (default `8.0`)
- `synth.similarity_floor` (default `0.75`)

Run manually: `python -m mslearn.synth_cli [--local]`.
For stable scheduling, keep judge queue concurrency low (often `1`) so
synthesis passes serialize cleanly.

## Server and exports

Run the API with `make serve` or:

    .venv/bin/uvicorn mslearn.server.app:create_app --factory --port 8000

`GET /api/memory` lists learner-memory items and
`DELETE /api/memory/{memory_id}` removes one. Both return `503` when learner
memory is unavailable, such as when mem0 is not installed or configured.

`POST /api/exports` with `{"kinds":["markdown","anki","graph"]}` writes files
under `data/exports/<timestamp>/`. Markdown exports are deterministic and use
cached teaching Markdown when present; otherwise they render summaries, claims,
quotes, conflicts, and citation footnotes directly from the graph. Anki exports
use stable deck/model IDs, and graph exports include both GraphML and JSON.

If `frontend/dist` exists, the API serves it at `/` after all API routers are
registered, so `/api/*` routes continue to take precedence.

## Frontend

The React UI lives in `frontend/` (Vite + TypeScript). During development, run
the API and Vite dev server separately — Vite proxies `/api` to port 8000:

    make serve                 # terminal 1
    cd frontend && npm run dev # terminal 2 → http://localhost:5173

Build for production (served by FastAPI from `frontend/dist`):

    make ui-build              # or: cd frontend && npm run build
    make serve

Frontend tests:

    make ui-test               # or: cd frontend && npm test
