from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import time
from collections import defaultdict

from mslearn.graph.records import CONFLICT_CLASSIFICATIONS, ConceptRecord
from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderBadOutputError

logger = logging.getLogger(__name__)

_CONCEPT_MATCH_SCHEMA = {"type": "object", "properties": {"matches": {"type": "array"}}}
_CONFLICT_SCAN_SCHEMA = {"type": "object", "properties": {"conflicts": {"type": "array"}}}
_CONCEPT_NAME_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "summary": {"type": "string"}},
}
_CONCEPT_DEPS_SCHEMA = {"type": "object", "properties": {"edges": {"type": "array"}}}
_CONCEPT_CATEGORIES_SCHEMA = {"type": "object", "properties": {"categories": {"type": "array"}}}
_MAX_CATEGORIZE_CONCEPTS = 200


def _resolve_match(value, candidate_ids: list[str]) -> str | None:
    """Map a model-returned match to a real candidate claim id, or None to drop.
    Accepts an exact claim id, or a 1-based index into the presented candidate
    list (the model often returns the list number instead of the id)."""
    if value in candidate_ids:
        return value
    try:
        idx = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= idx <= len(candidate_ids):
        return candidate_ids[idx - 1]
    return None


def _compute_anchor_matches(
    ctx, anchor: dict, *, candidate_k: int, similarity_floor: float, prompt: str, project_id: str
) -> tuple[str, list[dict], list[str], int]:
    """Phase A worker: compute one anchor's candidates and (if any) the
    model's match judgment for it. This is pure read + a single model call —
    it never reads or writes claim/concept assignment state, so the result
    for one anchor cannot be affected by what happens to any other anchor.
    That is what makes it safe to run concurrently across anchors: the
    candidate set only depends on embeddings/trust (fixed for the run) and
    the match judgment only depends on (anchor, candidates). Returns
    (anchor_id, candidates, matches, drops) so the caller can assemble the
    per-anchor results dict and sum drop counts without any shared mutable
    state.
    """
    anchor_id = anchor["claim_id"]
    hits = ctx.graph.vector_search_claims(anchor["embedding"], k=candidate_k + 1, project_id=project_id)
    candidates = [
        h
        for h in hits
        if h["claim_id"] != anchor_id
        and h["score"] >= similarity_floor
        and h.get("trust") in {"trusted", "escalated", "image_observed"}
    ]
    if not candidates:
        return anchor_id, [], [], 0

    candidate_ids = [c["claim_id"] for c in candidates]
    try:
        response = ctx.router.complete(
            "synthesis",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user",
                        content=_concept_match_prompt(prompt, anchor, candidates),
                    )
                ],
                json_schema=_CONCEPT_MATCH_SCHEMA,
                max_tokens=int(ctx.db.get_tunable("synth.max_tokens")),
            ),
        )
    except ProviderBadOutputError:
        # A malformed/truncated concept_match response (frequent with
        # deepseek-v4-flash on large corpora) must not crash the whole
        # clustering run -- degrade to "no match" for this anchor alone; it
        # falls back to getting its own concept in Phase B.
        logger.warning("concept_match call failed for anchor %s; degrading to no match", anchor_id)
        return anchor_id, candidates, [], 0
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    raw_matches = parsed.get("matches", [])
    matches: list[str] = []
    drops = 0
    for value in raw_matches:
        resolved = _resolve_match(value, candidate_ids)
        if resolved is None:
            logger.warning("dropped %s: %s", "match", f"claim {value!r} not in candidate set")
            drops += 1
        elif resolved not in matches:
            matches.append(resolved)
    return anchor_id, candidates, matches, drops


def cluster_new_claims(ctx, project_id: str = "default") -> set[str]:
    graph = ctx.graph
    db = ctx.db
    candidate_k = int(db.get_tunable("synth.candidate_k"))
    similarity_floor = db.get_tunable("synth.similarity_floor")
    prompt = get_prompt(db, "concept_match")
    known_concepts = {c["concept_id"] for c in graph.all_concepts(project_id=project_id)}
    dirty: set[str] = set()
    drops = 0

    anchors = list(graph.unassigned_trusted_claims(project_id=project_id))

    # Phase A: read-only, parallel. The blocking concept_match model call
    # for a given anchor depends only on (anchor, its candidates) -- never
    # on assignment state -- so all anchors' calls can run concurrently on a
    # thread pool sized by synth.concurrency. This was the dominant per-run
    # latency cost since each call blocks on the model. No assignment or
    # dirty-marking happens here; drop counts come back as per-anchor return
    # values (summed below in the main thread), not a shared counter, so
    # there is nothing here that requires locking.
    results: dict[str, tuple[list[dict], list[str]]] = {}
    if anchors:
        def _worker(anchor: dict) -> tuple[str, list[dict], list[str], int]:
            return _compute_anchor_matches(
                ctx,
                anchor,
                candidate_k=candidate_k,
                similarity_floor=similarity_floor,
                prompt=prompt,
                project_id=project_id,
            )

        width = int(db.get_tunable("synth.concurrency"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
            for anchor_id, candidates, matches, anchor_drops in pool.map(_worker, anchors):
                results[anchor_id] = (candidates, matches)
                drops += anchor_drops

    # Phase B: serial, deterministic, no model calls. Same anchor order and
    # same live graph.concept_id_of_claim reads as the old single-pass loop,
    # so an anchor already swept into an earlier anchor's cluster (via the
    # matched_unassigned bulk-assign below) is skipped here exactly as
    # before -- only the (order-independent) model judgment was precomputed
    # in Phase A; the mint/reuse/assign decisions and their ordering are
    # unchanged, so the resulting claim->concept assignments are identical
    # to the serial version.
    for anchor in anchors:
        anchor_id = anchor["claim_id"]
        if graph.concept_id_of_claim(anchor_id, project_id=project_id) is not None:
            continue

        candidates, matches = results.get(anchor_id, ([], []))

        if not candidates:
            concept_id = _mint_or_reuse_concept(graph, known_concepts, [anchor_id], project_id=project_id)
            graph.assign_claim(anchor_id, concept_id, project_id=project_id)
            graph.mark_concept_dirty(concept_id, True, project_id=project_id)
            dirty.add(concept_id)
            continue

        if not matches:
            concept_id = _mint_or_reuse_concept(graph, known_concepts, [anchor_id], project_id=project_id)
            graph.assign_claim(anchor_id, concept_id, project_id=project_id)
            graph.mark_concept_dirty(concept_id, True, project_id=project_id)
            dirty.add(concept_id)
            continue

        chosen_concept: str | None = None
        for candidate in candidates:
            cid = candidate["claim_id"]
            if cid not in matches:
                continue
            existing = graph.concept_id_of_claim(cid, project_id=project_id)
            if existing is not None:
                chosen_concept = existing
                break

        if chosen_concept is not None:
            graph.assign_claim(anchor_id, chosen_concept, project_id=project_id)
            graph.mark_concept_dirty(chosen_concept, True, project_id=project_id)
            dirty.add(chosen_concept)
            continue

        matched_unassigned = [
            claim_id for claim_id in matches if graph.concept_id_of_claim(claim_id, project_id=project_id) is None
        ]
        concept_id = _mint_or_reuse_concept(graph, known_concepts, [anchor_id, *matched_unassigned], project_id=project_id)
        graph.assign_claim(anchor_id, concept_id, project_id=project_id)
        for claim_id in matched_unassigned:
            graph.assign_claim(claim_id, concept_id, project_id=project_id)
        graph.mark_concept_dirty(concept_id, True, project_id=project_id)
        dirty.add(concept_id)

    if drops > 0:
        logger.warning("cluster_new_claims: dropped %d judge-provided item(s) total", drops)
    return dirty


def concept_match_claim_ids(ctx, anchor: dict, candidates: list[dict]) -> list[str]:
    """Return candidate claim ids judged to match anchor (same logic as clustering)."""
    if not candidates:
        return []
    prompt = get_prompt(ctx.db, "concept_match")
    candidate_ids = [c["claim_id"] for c in candidates]
    response = ctx.router.complete(
        "synthesis",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=_concept_match_prompt(prompt, anchor, candidates),
                )
            ],
            json_schema=_CONCEPT_MATCH_SCHEMA,
            max_tokens=int(ctx.db.get_tunable("synth.max_tokens")),
        ),
    )
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    raw_matches = parsed.get("matches", [])
    matches: list[str] = []
    for value in raw_matches:
        resolved = _resolve_match(value, candidate_ids)
        if resolved is not None and resolved not in matches:
            matches.append(resolved)
    return matches


def classify_conflict_pair(
    ctx, *, claim_a: dict, claim_b: dict, domain_profile: str
) -> str | None:
    """Classify tension between two claims using the conflict_scan prompt."""
    guidance = domain_guidance(domain_profile)
    prompt = get_prompt(ctx.db, "conflict_scan")
    claims = [claim_a, claim_b]
    response = ctx.router.complete(
        "synthesis",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=_conflict_scan_prompt(prompt, "eval-pair", claims, guidance),
                )
            ],
            json_schema=_CONFLICT_SCAN_SCHEMA,
            max_tokens=int(ctx.db.get_tunable("synth.max_tokens")),
        ),
    )
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    for row in parsed.get("conflicts", []):
        if not isinstance(row, dict):
            continue
        a = row.get("claim_a")
        b = row.get("claim_b")
        classification = row.get("classification")
        if {a, b} == {claim_a["claim_id"], claim_b["claim_id"]}:
            if classification in CONFLICT_CLASSIFICATIONS:
                return str(classification)
    return None


def _process_one_concept(
    ctx,
    concept_id: str,
    *,
    conflict_prompt: str,
    name_prompt: str,
    guidance: str,
    project_id: str,
) -> int:
    """Run the conflict-scan (if >=2 claims) and concept-name model calls for
    one concept, and apply their results to the graph. Returns the number of
    conflict items dropped for THIS concept (no shared mutable counter —
    callers sum these across concepts, which is what makes this safe to run
    from multiple threads concurrently: each call only touches its own
    concept_id in the graph)."""
    graph = ctx.graph
    db = ctx.db
    drops = 0
    claims = graph.claims_in_concept(concept_id, project_id=project_id)
    claim_ids = {c["claim_id"] for c in claims}

    if len(claims) >= 2:
        try:
            response = ctx.router.complete(
                "synthesis",
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="user",
                            content=_conflict_scan_prompt(
                                conflict_prompt, concept_id, claims, guidance
                            ),
                        )
                    ],
                    json_schema=_CONFLICT_SCAN_SCHEMA,
                    max_tokens=int(db.get_tunable("synth.max_tokens")),
                ),
            )
        except ProviderBadOutputError:
            # A malformed/truncated conflict_scan response must not crash
            # the whole synthesis run -- just skip conflict recording for
            # this concept and keep going.
            logger.warning("conflict_scan call failed for concept %s; skipping conflicts", concept_id)
            response = None
        if response is not None:
            parsed = response.parsed if isinstance(response.parsed, dict) else {}
            for row in parsed.get("conflicts", []):
                if not isinstance(row, dict):
                    continue
                claim_a = row.get("claim_a")
                claim_b = row.get("claim_b")
                classification = row.get("classification")
                rationale = row.get("rationale", "")
                if claim_a == claim_b:
                    logger.warning("dropped %s: %s", "conflict", f"self-pair {claim_a!r}")
                    drops += 1
                    continue
                if claim_a not in claim_ids or claim_b not in claim_ids:
                    logger.warning(
                        "dropped %s: %s",
                        "conflict",
                        f"claim(s) not in concept {concept_id!r}: {claim_a!r}, {claim_b!r}",
                    )
                    drops += 1
                    continue
                if classification not in CONFLICT_CLASSIFICATIONS:
                    logger.warning(
                        "dropped %s: %s",
                        "conflict",
                        f"unknown classification {classification!r}",
                    )
                    drops += 1
                    continue
                graph.add_conflict(claim_a, claim_b, classification, str(rationale), project_id=project_id)

    try:
        name_response = ctx.router.complete(
            "synthesis",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user", content=_concept_name_prompt(name_prompt, concept_id, claims)
                    )
                ],
                json_schema=_CONCEPT_NAME_SCHEMA,
                max_tokens=int(db.get_tunable("synth.max_tokens")),
            ),
        )
    except ProviderBadOutputError:
        # A malformed/truncated concept_name response must not crash the
        # run -- fall back to a deterministic name derived from the
        # concept's claims below instead of the model's judgment.
        logger.warning("concept_name call failed for concept %s; using fallback name", concept_id)
        parsed_name: dict = {}
    else:
        parsed_name = name_response.parsed if isinstance(name_response.parsed, dict) else {}

    name = str(parsed_name.get("name", "")).strip()
    if not name:
        name = _fallback_concept_name(claims)
    graph.set_concept_meta(
        concept_id,
        name=name,
        summary=str(parsed_name.get("summary", "")),
        project_id=project_id,
    )
    graph.mark_concept_dirty(concept_id, False, project_id=project_id)
    return drops


def _fallback_concept_name(claims: list[dict]) -> str:
    """Deterministic name to use when the model gives nothing (empty name,
    or the concept_name call failed entirely). Never the raw concept_id --
    that's an internal id (e.g. "k-cl123"), not something a learner should
    see in their study materials."""
    if claims:
        words = str(claims[0].get("text", "")).split()[:6]
        candidate = " ".join(words).strip()
        if candidate:
            return candidate
    return "Untitled concept"


def process_dirty_concepts(ctx, project_id: str = "default") -> int:
    graph = ctx.graph
    db = ctx.db
    dirty_ids = graph.dirty_concepts(project_id=project_id)
    total = len(dirty_ids)
    conflict_prompt = get_prompt(db, "conflict_scan")
    name_prompt = get_prompt(db, "concept_name")
    profile = get_domain_profile(db, project_id)
    guidance = domain_guidance(profile)

    # Concepts are independent (each touches only its own graph nodes/edges)
    # and this phase makes up to 2 blocking model calls per concept — the
    # dominant cost of a 78-minute incident run — so fan the work out over a
    # thread pool instead of a serial for-loop. `done` is a plain int guarded
    # by `progress_lock` (not an atomic/shared counter passed into the
    # helper) since it's the only piece of state genuinely shared across
    # threads; drop counts come back as per-concept return values instead.
    progress_lock = threading.Lock()
    done = 0

    def _run_and_report(concept_id: str) -> int:
        nonlocal done
        result = _process_one_concept(
            ctx,
            concept_id,
            conflict_prompt=conflict_prompt,
            name_prompt=name_prompt,
            guidance=guidance,
            project_id=project_id,
        )
        # Per-concept progress so the UI can show "Analyzing topics... n of
        # m" instead of a bare spinner — this phase runs two model calls per
        # concept and dominated the 78-minute incident run. Guarded by
        # progress_lock since multiple worker threads finish concurrently;
        # exact ordering of `done` values across threads doesn't matter, only
        # that it monotonically reaches `total`.
        with progress_lock:
            done += 1
            db.set_project_setting(
                project_id,
                "synthesis:progress",
                json.dumps(
                    {"phase": "analyzing", "done": done, "total": total, "ts": int(time.time())},
                    sort_keys=True,
                ),
            )
        return result

    drops = 0
    if dirty_ids:
        width = int(db.get_tunable("synth.concurrency"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
            futures = [pool.submit(_run_and_report, concept_id) for concept_id in dirty_ids]
            for future in concurrent.futures.as_completed(futures):
                drops += future.result()

    if drops > 0:
        logger.warning("process_dirty_concepts: dropped %d conflict item(s) total", drops)
    return len(dirty_ids)


def build_curriculum(ctx, project_id: str = "default") -> list[str]:
    graph = ctx.graph
    db = ctx.db
    all_concepts = {c["concept_id"]: c for c in graph.all_concepts(project_id=project_id)}
    spine_rows = graph.spine_concept_order(project_id=project_id)
    spine_ids = [r["concept_id"] for r in spine_rows]
    first_seq = {r["concept_id"]: int(r["first_seq"]) for r in spine_rows}

    deps = {
        (row["from_id"], row["to_id"])
        for row in graph.concept_dependencies(project_id=project_id)
        if row["from_id"] in set(spine_ids) and row["to_id"] in set(spine_ids)
    }
    drops = 0
    # Skip the one-shot deps DAG call for very large spines: it reliably
    # overflows max_tokens (hundreds of concepts) producing truncated JSON,
    # is low quality even when it doesn't, and the topo-sort below falls
    # back cleanly to natural (first_seq) spine order with no deps at all.
    if 2 <= len(spine_ids) <= 60:
        prompt = get_prompt(db, "concept_deps")
        try:
            response = ctx.router.complete(
                "synthesis",
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="user",
                            content=_concept_deps_prompt(prompt, spine_ids, all_concepts),
                        )
                    ],
                    json_schema=_CONCEPT_DEPS_SCHEMA,
                    max_tokens=int(db.get_tunable("synth.max_tokens")),
                ),
            )
        except ProviderBadOutputError:
            # A malformed/truncated concept_deps response must not crash
            # the run -- skip adding edges; the topo-sort below falls back
            # to natural spine order (deps stays as whatever was already
            # persisted from a previous run, if any).
            logger.warning("concept_deps call failed; falling back to natural spine order")
            response = None
        if response is not None:
            parsed = response.parsed if isinstance(response.parsed, dict) else {}
            for edge in parsed.get("edges", []):
                if not isinstance(edge, dict):
                    continue
                from_id = edge.get("from_concept")
                to_id = edge.get("to_concept")
                if from_id not in first_seq or to_id not in first_seq:
                    logger.warning(
                        "dropped %s: %s",
                        "edge",
                        f"concept(s) not in spine: {from_id!r} -> {to_id!r}",
                    )
                    drops += 1
                    continue
                if from_id == to_id:
                    logger.warning("dropped %s: %s", "edge", f"self-loop on {from_id!r}")
                    drops += 1
                    continue
                if not _acyclic_add(deps, (from_id, to_id)):
                    logger.warning(
                        "dropped %s: %s", "edge", f"cycle detected {from_id!r} -> {to_id!r}"
                    )
                    drops += 1
                else:
                    graph.add_depends_on(from_id, to_id, project_id=project_id)

    if drops > 0:
        logger.warning("build_curriculum: dropped %d edge(s) total", drops)
    ordered_spine = _topo_order(spine_ids, deps, first_seq)
    non_spine = sorted(
        (c for c in all_concepts if c not in set(spine_ids)),
        key=lambda cid: (all_concepts[cid].get("name", ""), cid),
    )
    ordered = ordered_spine + non_spine
    graph.set_concept_orders([(cid, idx) for idx, cid in enumerate(ordered)], project_id=project_id)
    return ordered


def assign_categories(ctx, project_id: str = "default") -> int:
    graph = ctx.graph
    db = ctx.db
    concepts = list(graph.all_concepts(project_id=project_id))
    valid_ids = {c["concept_id"] for c in concepts}
    if not (2 <= len(concepts) <= _MAX_CATEGORIZE_CONCEPTS):
        return 0
    prompt = get_prompt(db, "concept_categories")
    try:
        response = ctx.router.complete(
            "synthesis",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user",
                        content=_concept_categories_prompt(prompt, concepts),
                    )
                ],
                json_schema=_CONCEPT_CATEGORIES_SCHEMA,
                max_tokens=int(db.get_tunable("synth.max_tokens")),
            ),
        )
    except ProviderBadOutputError:
        # A malformed/truncated concept_categories response must not crash
        # synthesis -- leave categories empty and let the curriculum render
        # flat.
        logger.warning("concept_categories call failed; leaving categories empty")
        return 0
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in parsed.get("categories", []):
        if not isinstance(group, dict):
            continue
        name = str(group.get("name", "")).strip()
        if not name:
            continue
        for concept_id in group.get("concept_ids", []):
            if concept_id in valid_ids and concept_id not in seen:
                pairs.append((concept_id, name))
                seen.add(concept_id)
    if not pairs:
        return 0
    graph.set_concept_categories(pairs, project_id=project_id)
    return len(pairs)


def assign_sections(ctx, project_id: str = "default") -> int:
    """Roll each concept's claims' chunk section_path up to a home
    section_path on the concept: the most frequent non-empty path among its
    claims' chunks, tie-broken by earliest chunk seq. When a concept gets a
    non-empty section_path, its category is reconciled to section_path[0]
    (the top chapter) so category grouping stays consistent with structure.
    Concepts whose claims are all structure-less are left untouched. Returns
    the number of concepts assigned a non-empty section_path."""
    graph = ctx.graph
    paths_by_concept = graph.concept_section_paths(project_id=project_id)

    section_pairs: list[tuple[str, list[str]]] = []
    category_pairs: list[tuple[str, str]] = []
    for concept_id, entries in paths_by_concept.items():
        counts: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for path, seq in entries:
            if path:
                counts[tuple(path)].append(seq)
        if not counts:
            continue
        best_path = max(counts.items(), key=lambda kv: (len(kv[1]), -min(kv[1])))[0]
        section_pairs.append((concept_id, list(best_path)))
        category_pairs.append((concept_id, best_path[0]))

    graph.set_concept_sections(section_pairs, project_id=project_id)
    graph.set_concept_categories(category_pairs, project_id=project_id)
    return len(section_pairs)


def _ensure_concept(graph, known_concepts: set[str], concept_id: str, *, project_id: str = "default") -> None:
    if concept_id in known_concepts:
        return
    graph.upsert_concept(ConceptRecord(concept_id=concept_id, name=""), project_id=project_id)
    known_concepts.add(concept_id)


def _mint_or_reuse_concept(graph, known_concepts: set[str], claim_ids: list[str], *, project_id: str = "default") -> str:
    """Mint a concept id for claims that were unassigned when the caller last
    checked, or reuse whichever one of them a concurrent synthesis run
    assigned in the meantime.

    Concept ids must be sticky once assigned — Anki export guids and
    exported markdown are keyed off them, so re-minting a different id for
    claims that already belong somewhere would silently orphan/duplicate
    exports. Only genuinely new clusters (none of `claim_ids` assigned yet)
    get a freshly minted id.
    """
    for claim_id in claim_ids:
        existing = graph.concept_id_of_claim(claim_id, project_id=project_id)
        if existing is not None:
            return existing
    concept_id = f"k-{min(claim_ids)}"
    _ensure_concept(graph, known_concepts, concept_id, project_id=project_id)
    return concept_id


def _concept_match_prompt(base: str, anchor: dict, candidates: list[dict]) -> str:
    lines = [
        base,
        "",
        f"Anchor claim: {anchor['claim_id']} | {anchor['text']}",
        "Candidates:",
    ]
    for idx, row in enumerate(candidates, start=1):
        lines.append(
            f"{idx}. {row['claim_id']} | {row.get('text', '')} | stance={row.get('stance', '')}"
        )
    return "\n".join(lines)


def _conflict_scan_prompt(base: str, concept_id: str, claims: list[dict], guidance: str) -> str:
    lines = [base.replace("{domain_guidance}", guidance), "", f"Concept: {concept_id}", "Claims:"]
    for row in claims:
        lines.append(f"- {row['claim_id']} | stance={row['stance']} | {row['text']}")
    return "\n".join(lines)


def _concept_name_prompt(base: str, concept_id: str, claims: list[dict]) -> str:
    lines = [base, "", f"Concept: {concept_id}", "Claims:"]
    for row in claims:
        lines.append(f"- {row['claim_id']} | {row['text']}")
    return "\n".join(lines)


def _concept_deps_prompt(base: str, spine_ids: list[str], all_concepts: dict[str, dict]) -> str:
    lines = [base, "", "Concepts:"]
    for idx, concept_id in enumerate(spine_ids, start=1):
        name = all_concepts.get(concept_id, {}).get("name", "")
        lines.append(f"{idx}. {concept_id} | {name}")
    return "\n".join(lines)


def _concept_categories_prompt(base: str, concepts: list[dict]) -> str:
    lines = [base, "", "Concepts:"]
    for idx, c in enumerate(concepts, start=1):
        lines.append(f"{idx}. {c['concept_id']} | {c.get('name', '')}")
    return "\n".join(lines)


def _acyclic_add(edges: set[tuple[str, str]], new_edge: tuple[str, str]) -> bool:
    src, dst = new_edge
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
    if _reachable(graph, dst, src):
        return False
    edges.add(new_edge)
    return True


def _reachable(graph: dict[str, set[str]], start: str, target: str) -> bool:
    stack = [start]
    seen = set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, ()))
    return False


def _topo_order(
    nodes: list[str], deps: set[tuple[str, str]], first_seq: dict[str, int]
) -> list[str]:
    dependents = defaultdict(set)  # prerequisite -> dependents
    indegree = {n: 0 for n in nodes}
    for dependent, prerequisite in deps:
        if dependent not in indegree or prerequisite not in indegree:
            continue
        if dependent in dependents[prerequisite]:
            continue
        dependents[prerequisite].add(dependent)
        indegree[dependent] += 1

    ready = sorted((n for n, deg in indegree.items() if deg == 0), key=lambda n: (first_seq[n], n))
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
        ready.sort(key=lambda n: (first_seq[n], n))

    if len(ordered) != len(nodes):
        remaining = [n for n in nodes if n not in set(ordered)]
        ordered.extend(sorted(remaining, key=lambda n: (first_seq[n], n)))
    return ordered
