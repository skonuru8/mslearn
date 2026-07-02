import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "tr", "blockquote", "pre",
}
_SKIP_TAGS = {"script", "style", "head", "nav", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    raw = "".join(parser.parts)
    paragraphs = []
    for block in raw.split("\n\n"):
        cleaned = re.sub(r"\s+", " ", block).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)
