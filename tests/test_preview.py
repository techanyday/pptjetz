from __future__ import annotations

from app.services import preview


def test_is_pdf_cache_valid_false_when_missing_files(tmp_path):
    pptx = tmp_path / "deck.pptx"
    pdf = tmp_path / "deck.pdf"
    assert preview._is_pdf_cache_valid(pptx_path=str(pptx), pdf_path=str(pdf)) is False


def test_is_pdf_cache_valid_true_when_pdf_newer_than_pptx(tmp_path):
    pptx = tmp_path / "deck.pptx"
    pdf = tmp_path / "deck.pdf"
    pptx.write_bytes(b"x")
    pdf.write_bytes(b"%PDF-1.4")

    # Ensure mtimes are controllable
    (pptx).touch()
    (pdf).touch()
    import os

    os.utime(str(pptx), (100, 100))
    os.utime(str(pdf), (200, 200))

    assert preview._is_pdf_cache_valid(pptx_path=str(pptx), pdf_path=str(pdf)) is True


def test_is_pdf_cache_valid_false_when_pdf_older_than_pptx(tmp_path):
    pptx = tmp_path / "deck.pptx"
    pdf = tmp_path / "deck.pdf"
    pptx.write_bytes(b"x")
    pdf.write_bytes(b"%PDF-1.4")

    import os

    os.utime(str(pptx), (300, 300))
    os.utime(str(pdf), (200, 200))

    assert preview._is_pdf_cache_valid(pptx_path=str(pptx), pdf_path=str(pdf)) is False
