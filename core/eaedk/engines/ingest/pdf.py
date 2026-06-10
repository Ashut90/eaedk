"""PDF → text pages, via PyMuPDF (fitz). Optional dependency, imported lazily so the rest of
EAEDK runs without it. The extractors consume the returned ``Page`` list, so they're testable
without any PDF (and fitz can also *generate* a PDF for round-trip golden tests)."""
from __future__ import annotations

from .extract import Page


class PdfUnavailable(RuntimeError):
    pass


def pdf_to_pages(path: str) -> list[Page]:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - environment dependent
        raise PdfUnavailable(
            "PDF ingestion needs PyMuPDF. Install it with `pip install pymupdf`.") from e
    doc = fitz.open(path)
    try:
        return [Page(number=i + 1, text=page.get_text("text")) for i, page in enumerate(doc)]
    finally:
        doc.close()
