from pathlib import Path

import ebooklib
from ebooklib import epub as epub_lib

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.adapters.htmltext import html_to_text


_NAV_EXACT_NAMES = {"nav.xhtml", "toc.ncx"}


def _is_nav(item) -> bool:
    if isinstance(item, epub_lib.EpubNav):
        return True
    if "nav" in (getattr(item, "properties", None) or []):
        return True
    return item.get_name().lower() in _NAV_EXACT_NAMES


def _flatten_toc(toc, ancestors: tuple[str, ...] = ()) -> dict[str, tuple[str, ...]]:
    """Flatten ebooklib's nested `book.toc` into href -> section_path.

    `toc` items are either a `Link`, or a `(Section | Link, children)` tuple
    (children is itself a toc-shaped sequence). Ancestor titles accumulate
    on the way down.
    """
    result: dict[str, tuple[str, ...]] = {}
    for entry in toc:
        if isinstance(entry, tuple):
            node, children = entry
            path = ancestors + (node.title,)
            href = getattr(node, "href", "") or ""
            if href:
                result[href.split("#")[0]] = path
            result.update(_flatten_toc(children, path))
        else:
            path = ancestors + (entry.title,)
            result[entry.href.split("#")[0]] = path
    return result


def load_epub(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    book = epub_lib.read_epub(str(path))
    meta = book.get_metadata("DC", "title")
    title = meta[0][0] if meta else path.stem
    href_map = _flatten_toc(book.toc)
    units: list[StructuralUnit] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name()
        if _is_nav(item):
            continue
        text = html_to_text(item.get_content().decode("utf-8", errors="replace"))
        if not text:
            continue
        section_path = href_map.get(name, ())
        unit_title = section_path[-1] if section_path else name
        units.append(
            StructuralUnit(
                index=len(units), title=unit_title, text=text,
                locator=Locator(kind="href", href=name),
                section_path=section_path,
            )
        )
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="epub",
        role=role, title=title, units=units,
    )
