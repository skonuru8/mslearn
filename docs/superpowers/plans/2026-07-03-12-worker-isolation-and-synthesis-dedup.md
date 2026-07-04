# Plan 12 — Worker Isolation, Synthesis Dedup, Delete Races

**Status:** ready for implementation
**Depends on:** `3b53cb0` (Plan 11 + follow-ups)
**Source:** live read-only diagnosis 2026-07-03 20:49 of the stalled-ingest incident. Dead-lettered `celery`-queue messages already purged by the controller (2 `chunk_source_task` messages from pre-routing-fix re-adds; their source has since fully ingested 107/107).

## Incident verdict (evidence in diagnosis, summarized)

- "Zero chunks on re-add" = pre-`6f740b3` `chunk_source_task` messages published to the default `celery` queue nobody consumes. Fixed for new messages; dead ones purged.
- "4 chunks then stall for 10 min" = **worker starvation**: one worker process, `concurrency=2`, `prefetch=1`, `acks_late`, consuming BOTH queues. `synthesize_task` is enqueued from **three independent call sites with zero dedup** — Build button (`corpus.py:219`), `delete_source` side-effect (`corpus.py:176`), `try_complete_source` auto-fire (`tasks.py:115`). Pre-fix, each stacked run hung up to 300 s with no retry; two of them occupied both slots and starved `ingest`.
- Confirmed code gaps still open: (a) nothing prevents synthesis stacking; (b) synthesis still shares slots with extraction — a 10-minute reasoning run still halves or halts ingest throughput; (c) `chunk_source_task` checks source status only at entry, then runs multi-minute `load_source()`/`embed()` before writing — delete+re-add mid-flight lets a stale task clobber the fresh row (TOCTOU); (d) `extract_chunk_task` for a deleted source no-ops **silently** (`mark_chunk` returns early on missing row).

## Task 1 — Dedicated worker processes per queue (starvation fix)

`scripts/dev_up.sh` + `Makefile worker`: run TWO Celery workers —
- `-Q ingest --concurrency=2 -n ingest@%h`
- `-Q judge --concurrency=1 -n judge@%h`

Synthesis (judge) can then never occupy ingest slots. dev_up.sh: manage both pids in the existing trap/cleanup; `make worker` becomes `make worker` (ingest) + `make worker-judge`, or a single target starting both with a trap — pick whichever keeps Ctrl-C behavior clean. Update README run instructions.
`worker_online()` ping: verify it still reports true with named workers (control.ping is broadcast — fine), and that `/api/status` doesn't double-count.

## Task 2 — Synthesis enqueue dedup

Goal: N triggers while a run is active/queued collapse into at most one queued follow-up.

- New OpsDB helper `try_mark_synthesis_queued(project_id) -> bool`: atomic INSERT-or-check on a `synthesis:queued:{project}` setting with a timestamp; returns False when a fresh queued/running marker exists (queued marker < 15 min old, or `synthesis:running_since` < 30 min old). All three call sites route through one `enqueue_synthesis(project_id) -> bool` helper (new function in `worker/tasks.py` or `pipeline/orchestrator.py`) that checks the marker, then `.delay()`s.
- `synthesize_task` clears the queued marker when it starts (it already writes `running_since`) and re-checks nothing else — the task is incremental/idempotent so an occasional duplicate is wasteful, not harmful; markers are TTL-by-timestamp so a crashed worker can't wedge synthesis forever.
- Build button response: `{enqueued: true|false, already_running: bool, worker_online: …}`; UI says "Already building — hang tight" instead of silently queueing a duplicate.
- Tests: three rapid enqueues → one delay; marker expiry allows re-enqueue; task start clears marker.

## Task 3 — Close the chunk_source_task TOCTOU

After `load_source()` + `chunk_source()` + `embed()` complete (the slow part), **re-check** `source_row(source_id, project_id)`: if row missing or `status != "chunking"`, log one INFO line and abort WITHOUT writing (no graph upserts, no register_chunk_jobs, no status flip). Graph upserts happen after the re-check.
Note: `ts`/row identity — a re-added source has a fresh row with the same PK; distinguish incarnations by comparing the row `ts` captured at task start with the row `ts` at re-check (abort if changed). Pass nothing extra through Celery args — read both from DB.
Tests: simulate delete-mid-task (delete row between chunking and commit) → no writes, INFO logged; re-add-mid-task (row ts changed) → abort.

## Task 4 — Deleted-source extract tasks stop vanishing silently

`extract_chunk_task`: when `source_row` is None (deleted), log INFO `chunk %s skipped: source deleted` and return early — before touching the graph. `mark_chunk`'s early return on missing rows stays (correct), but the task shouldn't do extraction work for a deleted source at all (currently it proceeds to a full model call before failing on `get_chunk`).
Tests: deleted source → no router calls, one INFO line.

## Task 5 — Ops guard for future queue misroutes

`test_all_tasks_routed_to_consumed_queues` exists (asserts every `mslearn.*` task has a route to ingest/judge). Extend `worker_online()`/`/api/status` with a cheap dead-letter probe: `llen celery` via the existing broker connection (kombu) — if > 0, include `dead_letter_count` in `/api/status` and render a warning chip ("Some background jobs are unroutable — restart with latest code / contact support" plain-language equivalent). Keep it best-effort (exception → omit key).
Tests: status includes key when probe returns > 0 (mock).

## Conventions
Same as Plans 9–11 (Cypher in store.py, tunables via registry, offline tests, green suites per commit, conventional commits).

## Verification
1. Start stack → `celery@ingest` and `celery@judge` both up; worker chip green.
2. Add a source, press Build 5× fast → exactly one synthesis run; UI says already building.
3. During a synthesis run, add another source → chunks extract immediately (no starvation).
4. Delete a source mid-Preparing → no resurrection, one INFO line, re-add works clean.
5. `make check` + `make ui-test` green.
