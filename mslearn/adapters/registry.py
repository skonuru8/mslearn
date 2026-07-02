from pathlib import Path

from mslearn.adapters.base import SourceDocument
from mslearn.adapters.blog import load_blog
from mslearn.adapters.epub import load_epub
from mslearn.adapters.pdf import load_pdf

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
_SUFFIX_TYPES = {
    ".pdf": "pdf", ".epub": "epub",
    ".html": "blog", ".htm": "blog",
    ".mp3": "audio", ".m4a": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio",
}


def detect_source_type(ref: str) -> str:
    if ref.startswith(("http://", "https://")):
        return "youtube" if any(h in ref for h in _YOUTUBE_HOSTS) else "blog"
    source_type = _SUFFIX_TYPES.get(Path(ref).suffix.lower())
    if source_type is None:
        raise ValueError(f"cannot detect source type for {ref!r}")
    return source_type


def load_source(
    ref: str, *, source_type: str | None = None, role: str = "supplement", **kwargs
) -> SourceDocument:
    stype = source_type or detect_source_type(ref)
    if stype == "pdf":
        return load_pdf(ref, role)
    if stype == "epub":
        return load_epub(ref, role)
    if stype == "blog":
        return load_blog(ref, role)
    if stype == "youtube":
        from mslearn.adapters.youtube import load_youtube  # keeps yt deps lazy

        return load_youtube(ref, role, **kwargs)
    if stype == "audio":
        from mslearn.adapters.audio import load_audio

        return load_audio(ref, kwargs["transcriber"], role)
    raise ValueError(f"unknown source type {stype!r}")
