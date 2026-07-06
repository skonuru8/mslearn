# Plan 16 — Memory Resilience, mem0 Removal, Richer Notes

**Status:** ready for implementation
**Depends on:** `b7c2d79` (Plan 15 complete)
**Trigger:** live, 2026-07-06 — image ingestion worked (30 chunks → 10 DNS
concepts), but opening any concept, submitting a quiz answer, and asking a chat
question all return HTTP 500. Also: generated notes read as a vague bullet-point
overview.

## Root cause (one bug, three symptoms)

Teaching, quiz, and chat each call the learner memory directly and **unguarded**:
- `mslearn/pipeline/teaching.py:110` `memory.search(...)`
- `mslearn/pipeline/quiz.py:48,138,202,208` `search`/`add`
- `mslearn/server/routers/chat.py:135,164` `search`/`add`

`Mem0Memory` builds its mem0 client lazily on first `.search()`/`.add()`
(`memory/mem0_impl.py:23-29`). That build calls mem0's embedder factory, which
imports `mem0/embeddings/ollama.py` → `from ollama import Client` →
`ModuleNotFoundError` (the `ollama` pip package is not a declared dependency) →
mem0 then runs `input("The 'ollama' library is required. Install it now?")`,
which raises `EOFError` in a server with no stdin. The exception propagates out
of the interactive endpoint as a 500.

The `if memory is None` guards at each call site do NOT help: the object exists;
it only explodes when used. Note the **memory panel router already wraps every
call in try/except → 503** (`server/routers/memory.py:14-30`) — the fix pattern
exists; it was simply never applied to teach/quiz/chat.

The spec is explicit that memory is **personalization-only and advisory** ("if
memory and graph disagree the graph wins by construction", §3b). A memory
failure taking down teaching is an architecture violation, not just a missing
try/except.

## Part A — Memory can never break an interactive endpoint (mandatory, do first)

Make every memory read/write in an interactive path degrade to
"no personalization" on ANY exception. This alone stops all three 500s.

- Wrap the memory calls in `teaching.py::_format_memory_hints`,
  `chat.py::_format_memory_hints`, `chat.py::_record_interaction`,
  `quiz.py::_struggle_text`, and `quiz.py::_record_struggle` in try/except:
  reads return `"(none)"` / `[]`; writes become no-ops. Log once at WARNING via
  the `mslearn` logger (not per call — see the disable flag below).
- Belt-and-suspenders in the memory object itself (Part B's `SqliteMemory` and,
  while it still exists, `Mem0Memory`): a `_disabled` flag set on first failure
  so a broken backend short-circuits instead of re-attempting an expensive/
  broken client build on every request (today mem0 re-tries the failing build
  every call). Public methods return empty/no-op when disabled.
- Tests: a memory stub whose `search`/`add` raise → teach/quiz/chat still return
  200 with empty hints; the disable flag flips after the first failure.

## Part B — Replace mem0 with an in-house SQLite memory (root-cause fix)

mem0 brings a fragile dependency chain (undeclared `ollama` lib, interactive
`input()` prompts, an OpenAI/OpenRouter base-url config landmine already patched
once in `mem0_impl.py:60-74`, lazy validation). The spec requires *a* memory
layer, not mem0 specifically — mem0 was an implementation choice. Its only real
value-add (LLM-based memory consolidation) is overkill for "remember which
concepts confused the learner." Replace it with a small, reliable in-house
implementation using infrastructure the app already has.

- New `mslearn/memory/sqlite_memory.py::SqliteMemory(db, router)` implementing
  the existing `LearnerMemory` protocol (`memory/base.py`: `add`/`search`/`all`/
  `delete` + `MemoryItem`):
  - New OpsDB table `learner_memory(memory_id TEXT PK, project_id TEXT,
    category TEXT, text TEXT, embedding TEXT (json), created_ts REAL)`, all
    access under the existing `self._lock` discipline. `memory_id` = a uuid4.
  - `add`: embed the text via `router.embed([text])` (the app's existing working
    path — httpx to Ollama `/api/embed`, **no `ollama` pip lib**), store the row.
  - `search(query, k, project_id)`: embed the query, cosine-similarity in Python
    against this project's stored embeddings (learner memory is small — dozens to
    low-hundreds of rows), return top-k `MemoryItem`s. Reuse the existing
    `pipeline/trust.py::cosine` (or a shared helper) — do not reimplement.
  - `all`/`delete`: straightforward table ops, project-scoped.
  - Its own `_disabled`-on-failure guard (Part A) so even embedding outages don't
    surface.
- `worker/context.py::build_default_context`: construct `SqliteMemory(db, router)`
  instead of `Mem0Memory`; keep the try/except so memory=None if even this fails.
- Delete `mslearn/memory/mem0_impl.py` and remove the `mem0ai` dependency from
  `pyproject.toml`; refresh the venv (`pip install -e ".[dev]"`) and confirm
  nothing else imports mem0. Update `memory/__init__.py` / any export.
- Honors the spec contract: personalization-only (stores only interaction/
  struggle/preference text, never facts), inspectable + deletable (memory panel
  unchanged), semantic recall via embeddings. The memory-provenance eval is
  unaffected (it inspects generated output, not the memory backend).
- Tests: `SqliteMemory` add→search returns the item ranked by similarity; project
  isolation; all/delete; survives an embedding failure (returns []/no-op, sets
  disabled); memory panel endpoints work against it.

## Part C — Richer, detailed notes (not a vague overview)

The `teach_concept` prompt (`prompts.py`) yields a terse 3-section lesson
(`## Explanation / ## Worked example / ## Common misconception`). The user wants
thorough notes. Revise the prompt (registry entry, so it stays overridable):
- Instruct comprehensive study notes that cover EVERY supplied claim in detail —
  explain the substance of each, not a one-line summary; keep `[claim:<id>]`
  citations on every factual sentence; keep the worked example and misconception
  but expand them; use clear headings and sub-points.
- Keep the required `## Where sources disagree` section when conflicts exist
  (the post-check at `teaching.py:31-39` must still pass).
- Verify `teach.max_tokens` (8192) is ample; the terseness is prompt-driven, not
  budget — do not inflate blindly.
- Honest limit to document in the plan/README: notes are only as rich as the
  trust-gated claims extracted from the source. If extraction from an image was
  sparse, notes will be thinner — richer extraction is a possible follow-up, out
  of scope here. Do NOT loosen the trust gate to manufacture depth.
- Tests: teaching output for a multi-claim concept cites every claim id (fake
  router returns a template echoing the claims); the conflict-section post-check
  still holds.

## Part D — Synthesis warnings (no action / low priority)

The log lines `dropped match: claim 'N' not in candidate set` and
`dropped edge: concept(s) not in spine` are **working-as-designed defensive
handling**: the judge occasionally returns claim/concept ids outside the allowed
set and the code correctly drops them (`synthesis.py` hallucinated-id guards).
They are WARN-level, non-fatal, and did not cause the 500s. No fix required.
Optional future hardening: tighten the clustering prompt's id contract. Note this
in the plan; do not change behavior now.

## Conventions
Cypher in `graph/store.py`; tunables/prompts via the registry; every model call
logged; offline tests with fakes (no live model, no live mem0/ollama); graph
tests only via `make graph-test`. Suites green per commit; conventional commits
with the standard trailer. Do not push. Do not restart/kill the user's running
processes.

## Verification
1. `make check` + `make ui-test` + `make ui-build` green; `make graph-test` green.
2. With NO `ollama` pip package installed (the current reality), opening a
   concept, submitting a quiz answer, and asking a chat question all return 200
   — memory simply contributes no hints if unavailable, and works via SQLite
   otherwise.
3. `grep -r mem0 mslearn/` returns nothing; `mem0ai` gone from `pyproject.toml`.
4. Generated notes for a multi-claim concept are detailed and cite every claim.
5. Memory panel still lists/deletes items (now SQLite-backed).

## Implementation notes (done)

- Part A: `a05ef38` — teaching/quiz/chat memory reads/writes wrapped in
  try/except degrading to "(none)"/[]/no-op; `Mem0Memory._disabled` guard
  added.
- Part B: `6f792d7` — `SqliteMemory(db, router)` (`memory/sqlite_memory.py`),
  new `learner_memory` OpsDB table, `build_default_context` wired to it,
  `memory/mem0_impl.py` deleted, `mem0ai` removed from `pyproject.toml`, venv
  refreshed. `grep -r mem0 mslearn/` and the `pyproject.toml` dependency list
  are both clean.
- Part C: `2693042` — `teach_concept` prompt revised for comprehensive,
  per-claim notes; required section contract and per-sentence citations
  unchanged so `teaching.py`'s post-check still holds; `teach.max_tokens`
  left at 8192 (verified ample, not budget-starved).
- Part D: no code change, as specified — `dropped match`/`dropped edge`
  warnings in `synthesis.py` are confirmed working-as-designed defensive
  hallucinated-id guards, WARN-level and non-fatal. Not the cause of the
  live 500s (Part A was). No action taken.

Verification: `make check` — 339 passed, 22 skipped (up from the pre-Plan-16
baseline of 330 passed, 22 skipped). `make ui-test` — 33 passed, unaffected
(frontend untouched by this plan). `make graph-test` — 22 passed on the
disposable `neo4j-test` container (port 7690), which the target tears down
itself afterward; nothing in Parts A-C touches Cypher or the graph schema,
so this is an unsurprising green.
