#!/usr/bin/env python3
"""Generate tiny smoke-corpus artifacts (pdf + reuse blog fixture path)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "smoke"
OUT.mkdir(parents=True, exist_ok=True)

blog = ROOT / "tests" / "fixtures" / "blog.html"
if blog.exists():
    (OUT / "blog.html").write_text(blog.read_text())

try:
    import fitz
except ImportError:
    print("pymupdf not installed; skip pdf generation")
else:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Cache invalidation is one of the two hard problems.")
    pdf_path = OUT / "smoke.pdf"
    doc.save(pdf_path)
    doc.close()
    print(f"wrote {pdf_path}")

print(f"smoke corpus dir: {OUT}")
