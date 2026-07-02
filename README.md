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
