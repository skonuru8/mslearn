import re
from pathlib import Path

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


def load_text(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    units = [
        StructuralUnit(
            index=i, title="", text=p,
            locator=Locator(kind="para", para_index=i),
        )
        for i, p in enumerate(paragraphs)
    ]
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="text",
        role=role, title=path.stem, units=units,
    )
