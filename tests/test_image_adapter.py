import pytest

from mslearn.adapters.image import load_image, media_type_for
from mslearn.adapters.registry import detect_source_type, load_source
from mslearn.chunking import chunk_source

FAKE_MARKDOWN = (
    "# Dashboard\n\n"
    "Total revenue: $1.2M this quarter.\n\n"
    "[image: bar chart of revenue by month, December highest]\n\n"
    "Nested browser tab title: Quarterly Report — Acme"
)


def _fake_describe(image_bytes, media_type):
    # Assert the adapter handed us real bytes + a media type, then return
    # canned Markdown standing in for a vision model's transcription.
    assert isinstance(image_bytes, (bytes, bytearray)) and image_bytes
    assert media_type.startswith("image/")
    return FAKE_MARKDOWN


def _png(tmp_path, name="shot.png"):
    path = tmp_path / name
    # Minimal valid PNG header bytes; the adapter only reads/encodes bytes.
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fakepixeldata")
    return path


def test_detection_covers_image_suffixes():
    for ref in ("shot.png", "a.jpg", "b.jpeg", "c.webp", "d.gif", "e.bmp", "f.heic"):
        assert detect_source_type(ref) == "image"


def test_media_type_for():
    from pathlib import Path
    assert media_type_for(Path("a.png")) == "image/png"
    assert media_type_for(Path("a.jpg")) == "image/jpeg"
    assert media_type_for(Path("a.webp")) == "image/webp"


def test_load_image_builds_units_from_transcription(tmp_path):
    doc = load_image(_png(tmp_path), role="spine", describe=_fake_describe)
    assert doc.source_type == "image" and doc.role == "spine"
    texts = [u.text for u in doc.units]
    # Verbatim text and the bracketed visual description both survive as units.
    assert any("Total revenue: $1.2M" in t for t in texts)
    assert any(t.startswith("[image:") for t in texts)
    assert any("Nested browser tab title" in t for t in texts)
    assert all(u.locator.kind == "image" for u in doc.units)


def test_image_flows_through_chunking(tmp_path):
    doc = load_image(_png(tmp_path), describe=_fake_describe)
    chunks = chunk_source(doc)
    assert chunks and all(c.source_id == doc.source_id for c in chunks)


def test_registry_routes_image_with_describe(tmp_path):
    doc = load_source(str(_png(tmp_path)), role="supplement", describe=_fake_describe)
    assert doc.source_type == "image" and doc.units


def test_registry_image_without_describe_fails_readably(tmp_path):
    with pytest.raises(ValueError, match="no image describer available"):
        load_source(str(_png(tmp_path)), role="supplement")
