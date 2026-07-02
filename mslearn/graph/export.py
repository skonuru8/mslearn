import json
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "http://graphml.graphdrawing.org/xmlns"


def write_json(nodes: list[dict], rels: list[dict], path: Path | str) -> None:
    Path(path).write_text(
        json.dumps({"nodes": nodes, "relationships": rels}, indent=2, ensure_ascii=False)
    )


def write_graphml(nodes: list[dict], rels: list[dict], path: Path | str) -> None:
    ET.register_namespace("", _NS)
    root = ET.Element(f"{{{_NS}}}graphml")
    graph = ET.SubElement(root, f"{{{_NS}}}graph", edgedefault="directed")
    for node in nodes:
        el = ET.SubElement(graph, f"{{{_NS}}}node", id=node["id"])
        el.set("labels", ";".join(node["labels"]))
        for key, value in node["properties"].items():
            data = ET.SubElement(el, f"{{{_NS}}}data", key=key)
            data.text = "" if value is None else str(value)
    for i, rel in enumerate(rels):
        el = ET.SubElement(
            graph, f"{{{_NS}}}edge",
            id=f"e{i}", source=rel["start"], target=rel["end"],
        )
        el.set("label", rel["type"])
        for key, value in rel["properties"].items():
            data = ET.SubElement(el, f"{{{_NS}}}data", key=key)
            data.text = "" if value is None else str(value)
    ET.ElementTree(root).write(path, xml_declaration=True, encoding="unicode")
