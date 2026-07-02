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


def load_epub(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    book = epub_lib.read_epub(str(path))
    meta = book.get_metadata("DC", "title")
    title = meta[0][0] if meta else path.stem
    units: list[StructuralUnit] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name()
        if _is_nav(item):
            continue
        text = html_to_text(item.get_content().decode("utf-8", errors="replace"))
        if not text:
            continue
        units.append(
            StructuralUnit(
                index=len(units), title=name, text=text,
                locator=Locator(kind="href", href=name),
            )
        )
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="epub",
        role=role, title=title, units=units,
    )
