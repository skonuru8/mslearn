import base64
import io
from pathlib import Path
from typing import Callable

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id

# (image_bytes, media_type) -> Markdown transcription/description of the image.
# Injectable so tests never call a live model; the worker builds the default
# from the router's image role (image_describe_via_router).
Describe = Callable[[bytes, str], str]

_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".heic": "image/heic",
}

# Formats every vision model we route to (OpenRouter qwen3-vl, offline
# Ollama qwen2.5vl) can actually decode. Anything else (HEIC from iPhones,
# BMP) must be re-encoded before it's sent, or the provider 400s.
_REMOTE_SUPPORTED_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def media_type_for(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "image/png")


def normalize_for_vision(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Ensure image bytes are in a format vision models actually accept.

    Formats already in `_REMOTE_SUPPORTED_MEDIA_TYPES` pass through untouched
    (no re-encode, no quality loss, no wasted CPU). Anything else (HEIC, BMP,
    ...) is decoded with Pillow and re-encoded as JPEG.
    """
    if media_type in _REMOTE_SUPPORTED_MEDIA_TYPES:
        return image_bytes, media_type

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90)
        return out.getvalue(), "image/jpeg"
    except Exception as exc:
        raise ValueError(
            f"could not decode image of media type {media_type!r} for vision normalization: {exc}"
        ) from exc


def _units_from_markdown(markdown: str, ref: str) -> list[StructuralUnit]:
    blocks = [b.strip() for b in markdown.split("\n\n") if b.strip()]
    return [
        StructuralUnit(
            index=i, title="", text=b,
            locator=Locator(kind="image", url=ref, para_index=i),
        )
        for i, b in enumerate(blocks)
    ]


def load_image(ref: str | Path, role: str = "supplement", *, describe: Describe) -> SourceDocument:
    """Read an image into a SourceDocument via a multimodal model.

    `describe` receives the raw image bytes + media type and returns Markdown:
    all readable text transcribed verbatim (including text inside nested
    screenshots), plus bracketed descriptions of non-text visuals. The Markdown
    becomes the document text so the normal chunk -> extract -> trust-gate flow
    applies; image-derived claims are tiered `image_observed` downstream.
    """
    path = Path(ref)
    markdown = describe(path.read_bytes(), media_type_for(path))
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="image",
        role=role, title=path.stem, units=_units_from_markdown(markdown, str(path)),
    )


def image_describe_via_router(router, db) -> Describe:
    """Build the default `describe` that calls the router's `image` role."""
    from mslearn.prompts import get_prompt
    from mslearn.providers.base import ModelMessage, ModelRequest

    def describe(image_bytes: bytes, media_type: str) -> str:
        image_bytes, media_type = normalize_for_vision(image_bytes, media_type)
        data_url = f"data:{media_type};base64," + base64.b64encode(image_bytes).decode()
        request = ModelRequest(
            messages=[ModelMessage(
                role="user", content=get_prompt(db, "image_transcribe"), images=[data_url],
            )],
            max_tokens=int(db.get_tunable("image.max_tokens")),
        )
        return router.complete("image", request).text

    return describe
