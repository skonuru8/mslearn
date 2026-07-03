from __future__ import annotations

import logging
from collections import defaultdict

from mslearn.graph.records import CONFLICT_CLASSIFICATIONS, ConceptRecord
from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest

logger = logging.getLogger(__name__)

_CONCEPT_MATCH_SCHEMA = {"type": "object", "properties": {"matches": {"type": "array"}}}
_CONFLICT_SCAN_SCHEMA = {"type": "object", "properties": {"conflicts": {"type": "array"}}}
_CONCEPT_NAME_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "summary": {"type": "string"}},
}
_CONCEPT_DEPS_SCHEMA = {"type": "object", "properties": {"edges": {"type": "array"}}}


def cluster_new_claims(ctx) -> set[str]:
    graph = ctx.graph
    db = ctx.db
    candidate_k = int(db.get_tunable("synth.candidate_k"))
    similarity_floor = db.get_tunable("synth.similarity_floor")
    prompt = get_prompt(db, "concept_match")
    known_concepts = {c["concept_id"] for c in graph.all_concepts()}
    dirty: set[str] = set()
    drops = 0

    for anchor in graph.unassigned_trusted_claims():
        anchor_id = anchor["claim_id"]
        if graph.concept_id_of_claim(anchor_id) is not None:
            continue

        hits = graph.vector_search_claims(anchor["embedding"], k=candidate_k + 1)
        candidates = [
            h
            for h in hits
            if h["claim_id"] != anchor_id
            and h["score"] >= similarity_floor
            and h.get("trust") in {"trusted", "escalated"}
        ]
        if not candidates:
            concept_id = f"k-{anchor_id}"
            _ensure_concept(graph, known_concepts, concept_id)
            graph.assign_claim(anchor_id, concept_id)
            graph.mark_concept_dirty(concept_id, True)
            dirty.add(concept_id)
            continue

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
            ),
        )
        parsed = response.parsed if isinstance(response.parsed, dict) else {}
        raw_matches = parsed.get("matches", [])
        matches = [claim_id for claim_id in raw_matches if claim_id in candidate_ids]
        for dropped_id in raw_matches:
            if dropped_id not in candidate_ids:
                logger.warning("dropped %s: %s", "match", f"claim {dropped_id!r} not in candidate set")
                drops += 1

        if not matches:
            concept_id = f"k-{anchor_id}"
            _ensure_concept(graph, known_concepts, concept_id)
            graph.assign_claim(anchor_id, concept_id)
            graph.mark_concept_dirty(concept_id, True)
            dirty.add(concept_id)
            continue

        chosen_concept: str | None = None
        for candidate in candidates:
            cid = candidate["claim_id"]
            if cid not in matches:
                continue
            existing = graph.concept_id_of_claim(cid)
            if existing is not None:
                chosen_concept = existing
                break

        if chosen_concept is not None:
            graph.assign_claim(anchor_id, chosen_concept)
            graph.mark_concept_dirty(chosen_concept, True)
            dirty.add(chosen_concept)
            continue

        matched_unassigned = [
            claim_id for claim_id in matches if graph.concept_id_of_claim(claim_id) is None
        ]
        concept_id = f"k-{min([anchor_id, *matched_unassigned])}"
        _ensure_concept(graph, known_concepts, concept_id)
        graph.assign_claim(anchor_id, concept_id)
        for claim_id in matched_unassigned:
            graph.assign_claim(claim_id, concept_id)
        graph.mark_concept_dirty(concept_id, True)
        dirty.add(concept_id)

    if drops > 0:
        logger.warning("cluster_new_claims: dropped %d judge-provided item(s) total", drops)
    return dirty


def process_dirty_concepts(ctx) -> int:
    graph = ctx.graph
    db = ctx.db
    dirty_ids = graph.dirty_concepts()
    conflict_prompt = get_prompt(db, "conflict_scan")
    name_prompt = get_prompt(db, "concept_name")
    profile = get_domain_profile(db)
    guidance = domain_guidance(profile)
    drops = 0

    for concept_id in dirty_ids:
        claims = graph.claims_in_concept(concept_id)
        claim_ids = {c["claim_id"] for c in claims}

        if len(claims) >= 2:
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
                ),
            )
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
                graph.add_conflict(claim_a, claim_b, classification, str(rationale))

        name_response = ctx.router.complete(
            "synthesis",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user", content=_concept_name_prompt(name_prompt, concept_id, claims)
                    )
                ],
                json_schema=_CONCEPT_NAME_SCHEMA,
            ),
        )
        parsed_name = name_response.parsed if isinstance(name_response.parsed, dict) else {}
        graph.set_concept_meta(
            concept_id,
            name=str(parsed_name.get("name", "")),
            summary=str(parsed_name.get("summary", "")),
        )
        graph.mark_concept_dirty(concept_id, False)

    if drops > 0:
        logger.warning("process_dirty_concepts: dropped %d conflict item(s) total", drops)
    return len(dirty_ids)


def build_curriculum(ctx) -> list[str]:
    graph = ctx.graph
    db = ctx.db
    all_concepts = {c["concept_id"]: c for c in graph.all_concepts()}
    spine_rows = graph.spine_concept_order()
    spine_ids = [r["concept_id"] for r in spine_rows]
    first_seq = {r["concept_id"]: int(r["first_seq"]) for r in spine_rows}

    deps = {
        (row["from_id"], row["to_id"])
        for row in graph.concept_dependencies()
        if row["from_id"] in set(spine_ids) and row["to_id"] in set(spine_ids)
    }
    drops = 0
    if len(spine_ids) >= 2:
        prompt = get_prompt(db, "concept_deps")
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
            ),
        )
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
                graph.add_depends_on(from_id, to_id)

    if drops > 0:
        logger.warning("build_curriculum: dropped %d edge(s) total", drops)
    ordered_spine = _topo_order(spine_ids, deps, first_seq)
    non_spine = sorted(
        (c for c in all_concepts if c not in set(spine_ids)),
        key=lambda cid: (all_concepts[cid].get("name", ""), cid),
    )
    ordered = ordered_spine + non_spine
    for idx, concept_id in enumerate(ordered):
        graph.set_concept_meta(concept_id, order_index=idx)
    return ordered


def _ensure_concept(graph, known_concepts: set[str], concept_id: str) -> None:
    if concept_id in known_concepts:
        return
    graph.upsert_concept(ConceptRecord(concept_id=concept_id, name=""))
    known_concepts.add(concept_id)


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
