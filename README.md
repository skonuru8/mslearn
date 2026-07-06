# mslearn — Personal Multi-Source Learning System

Turn the things you want to learn from — **books (PDF/EPUB), blog posts, YouTube
videos, and podcasts/audio** — into one connected study guide. The app reads
your sources, pulls out individual factual claims (each one backed by a
verbatim quote so nothing is made up), groups them into concepts, spots where
your sources **disagree with each other**, and then teaches you, quizzes you,
and exports notes/Anki flashcards.

Spec: `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md`

---

## What the app does, in plain words

1. **You add sources.** Upload a PDF from your computer, or paste a link to a
   blog post or YouTube video.
2. **The app reads them in the background.** Each source is split into small
   chunks. A model extracts claims from each chunk. A *trust gate* rejects any
   claim whose supporting quote isn't literally in the text — this is the
   anti-hallucination guarantee.
3. **Synthesis builds the map.** Claims that say the same thing get grouped
   into a concept. Concepts get ordered into a curriculum (what to learn
   first). When two sources disagree, the conflict is kept and classified
   (outdated vs. genuine debate vs. context-dependent vs. evidence mismatch) —
   never silently merged.
4. **You study.** Browse the curriculum, read generated teaching (every factual
   sentence carries a `[claim:…]` citation you can click), take reasoning
   quizzes graded with explanations, or just chat with your corpus. The app
   remembers what confused you and adapts.
5. **You export.** Markdown notes, Anki `.apkg` decks, and a full graph dump
   (GraphML + JSON) so your knowledge is never locked in.

## Example scenarios

**Scenario 1 — study a course PDF.**
You upload `algorithms-lecture-notes.pdf` as your **main source**. The app
chunks it, extracts claims ("Merge sort runs in O(n log n) time" with the exact
supporting quote), and builds concepts like *Asymptotic notation* →
*Divide-and-conquer* → *Merge sort*, ordered so prerequisites come first. You
open a concept, read the teaching page, hit **Quiz me**, answer in free text,
and get graded with an explanation citing the exact claims.

**Scenario 2 — book + YouTube disagree.**
You add a nutrition book as the main source, then paste three YouTube links as
extra material. One video says "eat within an 8-hour window"; the book says
timing barely matters vs. total intake. The app puts both claims in the same
concept, marks a **conflict (genuine debate)**, and every teaching page for
that concept shows a "Where sources disagree" section presenting both sides
with citations — instead of pretending there's one answer.

**Scenario 3 — flag a bad claim.**
Reading a teaching page you spot a claim that's wrong or out of context. Click
**Flag**, give a reason. The claim is quarantined, the affected concept is
marked dirty and regenerated without it. Your flag also feeds the eval golden
sets.

**Scenario 4 — exam-week export.**
Before an exam you hit **Export** → get per-concept Markdown notes and an Anki
deck with stable card IDs (re-export never duplicates cards), plus the graph
dump. All deterministic — no model calls needed, works offline.

---

## Running the app

### Prerequisites
- Python 3.12+, Node 20+, Docker Desktop
- [Ollama](https://ollama.com) with models pulled:
  `ollama pull qwen3.5:9b && ollama pull nomic-embed-text`
- An OpenRouter API key (default profile) and/or Claude Code installed

### One-time setup
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env        # fill in MSL_OPENROUTER_API_KEY
make ui-build               # build the web UI once
```

### Every time you use it — four things must be running
```bash
make run        # starts all four below, in order, Ctrl-C stops both workers + API
```
Or run them separately (useful for debugging one process at a time — `make
worker` and `make worker-judge` each need their own terminal):
```bash
make services      # 1. Redis + Neo4j containers (browser: http://localhost:7474)
make worker        # 2. ingest worker — DOES THE ACTUAL READING/EXTRACTION
make worker-judge  # 3. judge worker — synthesis only, kept off the ingest queue
make serve         # 4. web app → http://localhost:8000
```

Ingest and synthesis run as **two separate Celery workers**, each consuming
its own queue (`ingest`, `judge`). A synthesis pass can take minutes of model
reasoning; if it shared worker slots with extraction it would stall every
other source's ingestion for the duration. Splitting the queues means adding
a source while a synthesis run is in flight still extracts immediately.

The first audio or caption-less-video ingest downloads a Whisper model;
transcription is serialized (one at a time) to protect memory.

> **Important:** `make serve` alone is not enough. Without both workers
> running, uploaded sources sit in the queue forever and **Run synthesis does
> nothing** — the button only *enqueues* a job; a worker is what executes it.
> The app header shows a "Background worker running" / "Worker offline" chip
> (`GET /api/admin/health`) so this is never silent.

### Using the web app
- **Corpus** page: upload a file or paste a URL (blog/YouTube). Role
  `spine` = your main source (its structure drives curriculum order);
  `supplement` = extra material that attaches to existing concepts.
  The progress column shows `done+failed/total` chunks per source.
- **Run synthesis**: builds/updates concepts + curriculum from extracted
  claims. Runs automatically when a source finishes; the button forces a pass.
  Needs the worker running and at least some trusted claims in the graph.
- **Curriculum / Concept** pages: ordered concepts, teaching with citations,
  conflict callouts, flagging.
- **Quiz**: free-text answers, graded with explanations, results feed memory.
- **Chat**: streaming Q&A over your corpus only — it will say "insufficient
  material" rather than invent an answer.
- **Memory** page: inspect/delete everything the app has learned about *you*
  (weak spots, preferences). Memory personalizes; it never supplies facts.
- **Admin** page: model spend/latency log, profile switch, tunable audit.

### Switching model backends
`profiles.yaml` defines named profiles: `openrouter` (default),
`claude-code`, `offline` (all-local via Ollama). Switch in the Admin UI or set
`MSL_PROFILE`. Model IDs live only in the YAML.

### Command-line alternatives
```bash
python -m mslearn.ingest_cli <file-or-url> --role spine --local  # ingest inline
python -m mslearn.synth_cli --local                              # synthesis inline
```
`--local` runs everything in-process (no worker needed) — fine for one small
source, slow for big ones.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Progress stuck at `0/N` | worker not running | `make worker` |
| `X failed` in progress column | extraction/model errors on those chunks | check source error (Corpus row), Admin → model calls log |
| `invalid JSON from ollama: ''` | model output truncated (thinking models spend the token budget before answering) | raise extraction max-tokens tunable; see plan `2026-07-03-09` |
| Source `paused` by itself | failure-rate monitor tripped (too many chunks failing) | fix the underlying error, then hit Resume |
| Run synthesis does nothing | no worker, or zero trusted claims yet | start worker; confirm ingestion produced claims |
| Chat answers "insufficient material" | corpus has no claims relevant to the question | ingest more sources / check ingestion succeeded |
| Neo4j warning spam in server logs | queries against an empty graph (missing labels/properties) | harmless when corpus is empty; noise suppression tracked in plan |

## Development

```bash
make check        # ruff + full offline pytest suite
make graph-test   # Neo4j integration tests — runs against a DISPOSABLE
                  # throwaway container (port 7690); these tests wipe the DB
                  # they target and never touch your real data
make ui-test      # frontend vitest suite
cd frontend && npm run dev   # hot-reload UI on :5173 (proxies /api to :8000)
```

## Evals — definition of done

Golden sets live in `evals/golden/*.jsonl` (rows with review status
`approved`/`corrected` only; seed + review via the UI eval pages).

```bash
.venv/bin/python -m mslearn.evals.run --offline     # CI-safe metric run
.venv/bin/python -m mslearn.evals.run               # + judged provenance
.venv/bin/python -m mslearn.evals.evolve_cli --once # eval-gated self-evolution
bash scripts/release_check.sh                       # full release harness
```

Release gates (spec §6): extraction P/R ≥ 0.90/0.85, grounding false-accept
≤ 2%, clustering F1 ≥ 0.80, tension accuracy ≥ 0.75, schema validity ≥ 0.99,
provenance violations = 0. Self-evolution proposals are accepted only if the
target metric improves **and** no gate regresses; every change is audited and
rollbackable (`POST /api/admin/tunables/{key}/rollback`).

## Architecture notes (for contributors)

- **Adapters** (`mslearn/adapters/`): PDF (PyMuPDF), EPUB (ebooklib), blog
  (trafilatura), YouTube (youtube-transcript-api → yt-dlp+Whisper fallback),
  audio (faster-whisper), and **images** (a multimodal model — `image` role in
  `profiles.yaml`). All normalize to `SourceDocument` with locators.
- **Images** (screenshots, slides, photos, diagrams — png/jpg/webp/gif/bmp/heic):
  a multimodal model reads all visible text (including text inside nested
  screenshots / browser windows) and describes non-text visuals; the result
  flows through the normal claim → concept → notes pipeline. Image-derived
  claims are labeled *from image* (an `image_observed` trust tier) because they
  are model-read, not verbatim quotes from an authored text. The openrouter
  profile uses `openai/gpt-4o-mini` for this; offline uses a local `qwen2.5vl`
  (needs `ollama pull qwen2.5vl:7b`).
- **Trust gate** (`mslearn/pipeline/trust.py`): rapidfuzz verbatim-quote check
  + embedding cosine sanity. Thresholds are audited tunables.
- **Graph** (`mslearn/graph/store.py`): all Cypher lives here. Labels
  `Source/Chunk/Claim/Concept`; edges `EXTRACTED_FROM/IN_CONCEPT/DEPENDS_ON/
  CONFLICTS_WITH{classification}`; native vector indexes (768-dim).
- **Pipeline** (`mslearn/pipeline/`): LangGraph extraction graph
  (extract→validate→retry→escalate), synthesis (vector-blocked clustering with
  judge verdicts → conflict scan → Kahn topological curriculum).
- **Workers** (`mslearn/worker/`): two dedicated Celery processes, one per
  queue (`ingest`, `judge`) — synthesis can never occupy an ingest slot,
  per-process context (fork-safe), durable chunk jobs in SQLite
  (`data/ops.db`, WAL).
- **Providers** (`mslearn/providers/`): Ollama / OpenRouter / Claude Code
  behind one `ModelProvider` interface; every call logged to `model_calls`.
- **Memory** (`mslearn/memory/`): mem0 on the same Neo4j, personalization
  only — an eval gate asserts no generated fact originates from memory.
