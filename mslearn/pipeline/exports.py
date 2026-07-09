from __future__ import annotations

import html
import re
from pathlib import Path

import genanki

from mslearn.graph.export import write_graphml, write_json

DECK_ID = 1607392319
MODEL_ID = 1607392320


def export_markdown(ctx, out_dir: Path | str, project_id: str = "default") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    index_lines = ["# mslearn Export", ""]
    for index, concept in enumerate(_curriculum(ctx.graph, project_id)):
        filename = f"{index:03d}-{_slug(concept['name'])}.md"
        path = out_dir / filename
        content = _render_concept_markdown(ctx.graph, concept, project_id)
        path.write_text(content)
        paths.append(path)
        index_lines.append(f"- [{concept['name']}]({filename})")

    index_path = out_dir / "_index.md"
    index_path.write_text("\n".join(index_lines).rstrip() + "\n")
    paths.append(index_path)
    return paths


def export_anki(ctx, out_path: Path | str, project_id: str = "default") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    model = genanki.Model(
        MODEL_ID,
        "mslearn Basic",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
            }
        ],
    )
    deck = genanki.Deck(DECK_ID, "mslearn")
    for concept in _curriculum(ctx.graph, project_id):
        concept_id = concept["concept_id"]
        claims = _claims(ctx.graph, concept_id, project_id)
        front = f"Explain: {concept['name']}"
        back = _anki_explanation_back(concept, claims)
        deck.add_note(_note(model, front, back, f"concept:{concept_id}"))

        claim_by_id = {claim["claim_id"]: claim for claim in claims}
        for conflict in ctx.graph.conflicts_in_concept(concept_id, project_id=project_id):
            front = f"Where do sources disagree on {concept['name']}?"
            back = _anki_conflict_back(conflict, claim_by_id, ctx.graph, project_id)
            deck.add_note(
                _note(
                    model,
                    front,
                    back,
                    f"conflict:{concept_id}:{conflict['claim_a']}:{conflict['claim_b']}",
                )
            )

    genanki.Package(deck).write_to_file(out_path)
    return out_path


def export_graph(ctx, out_dir: Path | str, project_id: str = "default") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes, rels = ctx.graph.export_all(project_id=project_id)
    graphml_path = out_dir / "graph.graphml"
    json_path = out_dir / "graph.json"
    write_graphml(nodes, rels, graphml_path)
    write_json(nodes, rels, json_path)
    return [graphml_path, json_path]


def _curriculum(graph, project_id: str = "default") -> list[dict]:
    concepts = graph.curriculum(project_id=project_id)
    if concepts:
        return concepts
    return sorted(
        graph.all_concepts(project_id=project_id),
        key=lambda row: (row.get("order_index") is None, row["concept_id"]),
    )


def _render_concept_markdown(graph, concept: dict, project_id: str = "default") -> str:
    concept_id = concept["concept_id"]
    claims = _claims(graph, concept_id, project_id)
    citations = _citation_map(graph, claims, project_id)
    lines = [
        f"# {concept['name']}",
        "",
        concept.get("summary", ""),
        "",
        "## Key Claims",
        "",
    ]
    if claims:
        for claim in claims:
            suffix = _footnote_suffix(claim["claim_id"], citations)
            lines.append(f"- {claim['text']}{suffix}")
            if claim.get("quote"):
                lines.append(f"  > {claim['quote']}")
    else:
        lines.append("- No trusted claims available.")

    conflicts = graph.conflicts_in_concept(concept_id, project_id=project_id)
    if conflicts:
        lines.extend(["", "## Conflicts", ""])
        claim_by_id = {claim["claim_id"]: claim for claim in claims}
        for conflict in conflicts:
            left = claim_by_id.get(conflict["claim_a"], {"text": conflict["claim_a"]})
            right = claim_by_id.get(conflict["claim_b"], {"text": conflict["claim_b"]})
            lines.append(
                f"- {conflict['classification']}: {conflict['rationale']} "
                f"({left['text']}{_footnote_suffix(conflict['claim_a'], citations)} vs "
                f"{right['text']}{_footnote_suffix(conflict['claim_b'], citations)})"
            )

    if citations:
        lines.extend(["", "## Citations", ""])
        for claim_id, citation in sorted(citations.items(), key=lambda item: item[1]["number"]):
            lines.append(f"[^{citation['number']}]: {_format_locator(citation)}")

    return "\n".join(lines).rstrip() + "\n"


def _claims(graph, concept_id: str, project_id: str = "default") -> list[dict]:
    return [
        claim
        for claim in graph.claims_in_concept(concept_id, project_id=project_id)
        if claim.get("trust", "trusted") in {"trusted", "escalated", "image_observed"}
    ]


def _citation_map(
    graph, claims: list[dict], project_id: str = "default"
) -> dict[str, dict]:
    rows = graph.citations_for_claims(
        [claim["claim_id"] for claim in claims], project_id=project_id
    )
    return {
        row["claim_id"]: {**row, "number": index}
        for index, row in enumerate(rows, start=1)
    }


def _footnote_suffix(claim_id: str, citations: dict[str, dict]) -> str:
    citation = citations.get(claim_id)
    if citation is None:
        return ""
    return f" [^{citation['number']}]"


def _format_locator(citation: dict) -> str:
    parts = [citation["source_id"]]
    if citation.get("kind"):
        parts.append(str(citation["kind"]))
    if citation.get("seq") is not None:
        parts.append(f"seq {citation['seq']}")
    if citation.get("page") is not None:
        parts.append(f"page {citation['page']}")
    if citation.get("para_index") is not None:
        parts.append(f"paragraph {citation['para_index']}")
    if citation.get("href"):
        parts.append(str(citation["href"]))
    if citation.get("url"):
        parts.append(str(citation["url"]))
    if citation.get("start_s") is not None and citation.get("end_s") is not None:
        parts.append(f"{citation['start_s']}-{citation['end_s']}s")
    return ", ".join(parts)


def _anki_explanation_back(concept: dict, claims: list[dict]) -> str:
    lines = [html.escape(concept.get("summary", ""))]
    if claims:
        lines.append("<ul>")
        for claim in claims:
            lines.append(f"<li>{html.escape(claim['text'])}</li>")
        lines.append("</ul>")
    return "\n".join(lines)


def _anki_conflict_back(
    conflict: dict, claim_by_id: dict[str, dict], graph, project_id: str = "default"
) -> str:
    citations = _citation_map(
        graph,
        [
            claim_by_id[claim_id]
            for claim_id in (conflict["claim_a"], conflict["claim_b"])
            if claim_id in claim_by_id
        ],
        project_id,
    )
    lines = [
        f"{html.escape(conflict['classification'])}: {html.escape(conflict['rationale'])}",
        "<ul>",
    ]
    for claim_id in (conflict["claim_a"], conflict["claim_b"]):
        claim = claim_by_id.get(claim_id)
        if claim is None:
            continue
        locator = _format_locator(citations[claim_id]) if claim_id in citations else claim.get("source_id", "")
        lines.append(f"<li>{html.escape(claim['text'])} ({html.escape(locator)})</li>")
    lines.append("</ul>")
    return "\n".join(lines)


def _note(model, front: str, back: str, guid_seed: str) -> genanki.Note:
    return genanki.Note(
        model=model,
        fields=[front, back],
        guid=genanki.guid_for("mslearn", guid_seed),
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "concept"
