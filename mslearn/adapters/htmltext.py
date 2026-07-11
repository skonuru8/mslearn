import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "tr", "blockquote", "pre",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style", "head", "nav", "noscript"}


class _SegmentExtractor(HTMLParser):
    """Splits HTML into (section_path, text) segments.

    Headings (h1-h6) don't produce their own text segment — they update a
    level stack (pop to `level-1`, push the heading title) that tags every
    subsequent body block until the next heading. Other block tags act as
    paragraph boundaries, same as the old flat extractor.
    """

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._stack: list[tuple[int, str]] = []
        self._heading_level: int | None = None
        self._heading_buf: list[str] = []
        self._buf: list[str] = []
        self.segments: list[tuple[tuple[str, ...], str]] = []

    def _flush_buf(self) -> None:
        cleaned = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if cleaned:
            path = tuple(title for _, title in self._stack)
            self.segments.append((path, cleaned))

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif not self._skip_depth:
            if tag in _HEADING_TAGS:
                self._flush_buf()
                self._heading_level = int(tag[1])
                self._heading_buf = []
            elif tag in _BLOCK_TAGS:
                self._flush_buf()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif not self._skip_depth:
            if tag in _HEADING_TAGS and self._heading_level is not None:
                level = self._heading_level
                title = re.sub(r"\s+", " ", "".join(self._heading_buf)).strip()
                self._heading_level = None
                self._heading_buf = []
                if title:
                    while self._stack and self._stack[-1][0] >= level:
                        self._stack.pop()
                    self._stack.append((level, title))
            elif tag in _BLOCK_TAGS:
                self._flush_buf()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._heading_level is not None:
            self._heading_buf.append(data)
        else:
            self._buf.append(data)


def html_to_segments(html: str) -> list[tuple[tuple[str, ...], str]]:
    parser = _SegmentExtractor()
    parser.feed(html)
    parser.close()
    parser._flush_buf()
    return parser.segments


def html_to_text(html: str) -> str:
    return "\n\n".join(text for _, text in html_to_segments(html))
