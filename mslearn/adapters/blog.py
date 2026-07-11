import re
from pathlib import Path

import httpx
import trafilatura

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.adapters.htmltext import html_to_segments


class BlogExtractionError(Exception):
    """trafilatura found no extractable article content."""


def _title_of(html: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else fallback


def load_blog_html(html: str, url: str, role: str = "supplement") -> SourceDocument:
    extracted = trafilatura.extract(html, output_format="html")
    if not extracted:
        raise BlogExtractionError(f"no extractable content at {url!r}")
    segments = html_to_segments(extracted)
    if not segments:
        raise BlogExtractionError(f"no extractable content at {url!r}")
    units = [
        StructuralUnit(
            index=i, title="", text=text,
            locator=Locator(kind="url", url=url, para_index=i),
            section_path=section_path,
        )
        for i, (section_path, text) in enumerate(segments)
    ]
    return SourceDocument(
        source_id=make_source_id(url), source_type="blog",
        role=role, title=_title_of(html, url), units=units,
    )


def load_blog(ref: str, role: str = "supplement") -> SourceDocument:
    if ref.startswith(("http://", "https://")):
        resp = httpx.get(ref, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        return load_blog_html(resp.text, url=ref, role=role)
    path = Path(ref)
    return load_blog_html(path.read_text(encoding="utf-8"), url=str(path), role=role)
