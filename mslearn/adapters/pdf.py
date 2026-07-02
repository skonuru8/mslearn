from pathlib import Path

import fitz  # PyMuPDF

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


def load_pdf(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    doc = fitz.open(path)
    try:
        title = ((doc.metadata or {}).get("title") or "").strip() or path.stem
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
                )
            )
    finally:
        doc.close()
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="pdf",
        role=role, title=title, units=units,
    )
