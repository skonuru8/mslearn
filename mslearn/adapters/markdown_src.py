import re
from pathlib import Path

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def load_markdown(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")

    units: list[StructuralUnit] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        units.append(
            StructuralUnit(
                index=len(units), title="", text=text,
                locator=Locator(kind="para", para_index=len(units)),
                section_path=tuple(title for _, title in stack),
            )
        )

    for line in raw.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()

    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="markdown",
        role=role, title=path.stem, units=units,
    )
