import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class Locator:
    kind: str  # "page" | "href" | "url" | "time"
    page: int | None = None          # PDF, 1-based
    href: str | None = None          # EPUB internal document name
    url: str | None = None           # blog / video source URL
    para_index: int | None = None    # paragraph index within the unit
    start_s: float | None = None     # audio/video timestamps (seconds)
    end_s: float | None = None


@dataclass
class StructuralUnit:
    index: int
    title: str
    text: str
    locator: Locator


@dataclass
class SourceDocument:
    source_id: str
    source_type: str  # "pdf" | "epub" | "blog" | "youtube" | "audio"
    role: str         # "spine" | "supplement"
    title: str
    units: list[StructuralUnit] = field(default_factory=list)

    def full_text(self) -> str:
        return "\n\n".join(u.text for u in self.units if u.text)


def make_source_id(ref: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", ref.lower()).strip("-")[-40:]
    digest = hashlib.sha256(ref.encode()).hexdigest()[:8]
    return f"{stem}-{digest}"
