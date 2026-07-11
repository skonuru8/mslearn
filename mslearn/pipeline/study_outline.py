"""Pure tree builder for the study outline: nests curriculum() rows into a
chapter -> section -> concept tree by each concept's section_path, so the
outline endpoint (server/routers/study.py) and its tests never touch the DB.
"""


def build_outline(rows: list[dict]) -> dict:
    order_by_concept = {row["concept_id"]: row.get("order_index") or 0 for row in rows}
    nodes: dict[tuple[str, ...], dict] = {}
    tree: list[dict] = []
    flat: list[dict] = []
    # min order_index of any concept beneath each node's path, so chapters,
    # sections, and their concepts can all be sorted by book order below.
    node_min_order: dict[tuple[str, ...], int] = {}

    def ensure_node(path: tuple[str, ...]) -> dict:
        node = nodes.get(path)
        if node is not None:
            return node
        node = {"title": path[-1], "concepts": [], "children": []}
        nodes[path] = node
        if len(path) == 1:
            tree.append(node)
        else:
            parent = ensure_node(path[:-1])
            parent["children"].append(node)
        return node

    for row in rows:
        section_path = tuple(row.get("section_path") or ())
        concept = {
            "concept_id": row["concept_id"],
            "name": row.get("name", ""),
            "conflict_count": row.get("conflict_count", 0),
        }
        if not section_path:
            flat.append(concept)
            continue
        node = ensure_node(section_path)
        node["concepts"].append(concept)
        order_index = row.get("order_index") or 0
        for depth in range(1, len(section_path) + 1):
            prefix = section_path[:depth]
            node_min_order[prefix] = min(node_min_order.get(prefix, order_index), order_index)

    def sort_children(node_list: list[dict], prefix: tuple[str, ...]) -> None:
        node_list.sort(key=lambda n: node_min_order.get(prefix + (n["title"],), 0))
        for n in node_list:
            n["concepts"].sort(key=lambda c: order_by_concept.get(c["concept_id"], 0))
            sort_children(n["children"], prefix + (n["title"],))

    sort_children(tree, ())
    flat.sort(key=lambda c: order_by_concept.get(c["concept_id"], 0))

    return {"tree": tree, "flat": flat, "has_structure": bool(tree)}
