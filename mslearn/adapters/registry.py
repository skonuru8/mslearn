from pathlib import Path
from urllib.parse import urlparse

from mslearn.adapters.base import SourceDocument
from mslearn.adapters.blog import load_blog
from mslearn.adapters.epub import load_epub
from mslearn.adapters.pdf import load_pdf
from mslearn.adapters.text import load_text

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
_SUFFIX_TYPES = {
    ".pdf": "pdf", ".epub": "epub",
    ".html": "blog", ".htm": "blog",
    ".mp3": "audio", ".m4a": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".gif": "image", ".bmp": "image", ".heic": "image",
    ".txt": "text", ".md": "markdown", ".markdown": "markdown", ".docx": "docx",
}


def _is_youtube(ref: str) -> bool:
    host = (urlparse(ref).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS)


def detect_source_type(ref: str) -> str:
    if ref.startswith(("http://", "https://")):
        return "youtube" if _is_youtube(ref) else "blog"
    source_type = _SUFFIX_TYPES.get(Path(ref).suffix.lower())
    if source_type is None:
        raise ValueError(f"cannot detect source type for {ref!r}")
    return source_type


def load_source(
    ref: str,
    *,
    source_type: str | None = None,
    role: str = "supplement",
    transcriber=None,
    describe=None,
    **kwargs,
) -> SourceDocument:
    stype = source_type or detect_source_type(ref)
    if stype == "pdf":
        return load_pdf(ref, role)
    if stype == "epub":
        return load_epub(ref, role)
    if stype == "blog":
        return load_blog(ref, role)
    if stype == "text":
        return load_text(ref, role)
    if stype == "youtube":
        from mslearn.adapters.youtube import load_youtube  # keeps yt deps lazy

        # A transcriber is only needed when captions are missing; pass it
        # through so load_youtube can fall back, and let it raise its own
        # clear TranscriptUnavailable if captions fail and none was provided.
        return load_youtube(ref, role, transcriber=transcriber, **kwargs)
    if stype == "audio":
        from mslearn.adapters.audio import load_audio

        # Audio always needs a transcriber. Fail with a readable message
        # instead of a bare KeyError when the worker/CLI has none wired.
        if transcriber is None:
            raise ValueError(
                f"cannot transcribe audio source {ref!r}: no transcriber available "
                "(the worker builds one automatically; check whisper install/config)"
            )
        return load_audio(ref, transcriber, role)
    if stype == "image":
        from mslearn.adapters.image import load_image

        # A multimodal model reads the image; the worker builds `describe`
        # from the router's image role. Fail readably (not a bare error) when
        # none is wired.
        if describe is None:
            raise ValueError(
                f"cannot read image source {ref!r}: no image describer available "
                "(the worker builds one automatically from the profile's image role)"
            )
        return load_image(ref, role, describe=describe)
    raise ValueError(f"unknown source type {stype!r}")
