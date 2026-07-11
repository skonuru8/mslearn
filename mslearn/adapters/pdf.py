from pathlib import Path

import fitz  # PyMuPDF

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


def _path_for_page(toc: list[list], page_no: int) -> tuple[str, ...]:
    """Section path for `page_no`, from a `doc.get_toc(simple=True)` list.

    Walks toc entries at/before this page, maintaining a level stack: an
    entry pops any stack top at/deeper than its own level before pushing.
    """
    stack: list[tuple[int, str]] = []
    for level, title, entry_page in toc:
        if entry_page > page_no:
            break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    return tuple(title for _, title in stack)


def load_pdf(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    doc = fitz.open(path)
    try:
        title = ((doc.metadata or {}).get("title") or "").strip() or path.stem
        toc = doc.get_toc(simple=True)
        units: list[StructuralUnit] = []
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if not text:
                continue
            units.append(
                StructuralUnit(
                    index=len(units),
                    title=f"Page {page_no}",
                    text=text,
                    locator=Locator(kind="page", page=page_no),
                    section_path=_path_for_page(toc, page_no),
                )
            )
    finally:
        doc.close()
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="pdf",
        role=role, title=title, units=units,
    )
