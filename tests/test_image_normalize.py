"""normalize_for_vision: unsupported image formats (HEIC/BMP) must be
re-encoded to JPEG before being sent to a vision model, since no OpenRouter
or Ollama vision model reliably accepts HEIC (png/jpeg/webp/gif only)."""
import io

import pytest
from PIL import Image

from mslearn.adapters.image import image_describe_via_router, normalize_for_vision
from mslearn.opsdb import OpsDB
from mslearn.providers.base import ModelResponse


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, format="JPEG")
    return buf.getvalue()


def _heic_bytes() -> bytes:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(buf, format="HEIF")
    return buf.getvalue()


def test_png_passthrough():
    raw = _png_bytes()
    out_bytes, out_type = normalize_for_vision(raw, "image/png")
    assert out_bytes == raw
    assert out_type == "image/png"


def test_jpeg_passthrough():
    raw = _jpeg_bytes()
    out_bytes, out_type = normalize_for_vision(raw, "image/jpeg")
    assert out_bytes == raw
    assert out_type == "image/jpeg"


def test_heic_converts_to_jpeg():
    raw = _heic_bytes()
    out_bytes, out_type = normalize_for_vision(raw, "image/heic")
    assert out_type == "image/jpeg"
    assert out_bytes != raw
    reopened = Image.open(io.BytesIO(out_bytes))
    assert reopened.format == "JPEG"


def test_bmp_converts_to_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "yellow").save(buf, format="BMP")
    raw = buf.getvalue()

    out_bytes, out_type = normalize_for_vision(raw, "image/bmp")
    assert out_type == "image/jpeg"
    assert out_bytes != raw
    reopened = Image.open(io.BytesIO(out_bytes))
    assert reopened.format == "JPEG"


def test_corrupt_bytes_raises_valueerror():
    with pytest.raises(ValueError, match="image/heic"):
        normalize_for_vision(b"not an image", "image/heic")


class _CapturingRouter:
    """Records the ModelRequest passed to complete(), so tests can inspect
    the data URL actually sent to the vision model."""

    def __init__(self):
        self.last_request = None

    def complete(self, role, request):
        self.last_request = request
        return ModelResponse(
            text="described", parsed=None, input_tokens=1, output_tokens=1,
            latency_ms=1.0, provider="fake", model="m",
        )


def test_describe_sends_jpeg_data_url_for_heic_input(tmp_path):
    router = _CapturingRouter()
    db = OpsDB(tmp_path / "ops.db")
    describe = image_describe_via_router(router, db)

    heic_bytes = _heic_bytes()
    result = describe(heic_bytes, "image/heic")

    assert result == "described"
    sent_image = router.last_request.messages[0].images[0]
    assert sent_image.startswith("data:image/jpeg;base64,")
