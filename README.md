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
