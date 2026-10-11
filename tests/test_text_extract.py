from __future__ import annotations

import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ninova_mcp.text_extract import (
    extract_text_from_bytes,
    extract_text_from_path,
    guess_extension,
)


def _minimal_docx(paragraph: str) -> bytes:
    """Build a tiny DOCX (zip of XML parts) without depending on python-docx for setup."""
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


class TextExtractTests(unittest.TestCase):
    def test_guess_extension(self) -> None:
        self.assertEqual(guess_extension(filename="notes.PDF"), ".pdf")
        self.assertEqual(guess_extension(content_type="application/pdf"), ".pdf")
        self.assertEqual(
            guess_extension(url="https://ninova.itu.edu.tr/files/a.docx?x=1"),
            ".docx",
        )

    def test_extract_plain_text(self) -> None:
        result = extract_text_from_bytes(b"Merhaba Ninova\n", extension=".txt", max_chars=100)
        self.assertTrue(result["ok"])
        self.assertIn("Merhaba", result["text"])
        self.assertFalse(result["truncated"])

    def test_truncate(self) -> None:
        result = extract_text_from_bytes(b"abcdefghij", extension=".txt", max_chars=4)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "abcd")
        self.assertTrue(result["truncated"])

    def test_unsupported(self) -> None:
        result = extract_text_from_bytes(b"\x00\x01", extension=".bin")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["text"])

    def test_extract_docx(self) -> None:
        data = _minimal_docx("Odev aciklamasi burada")
        result = extract_text_from_bytes(data, extension=".docx")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIn("Odev aciklamasi", result["text"])

    def test_extract_from_path(self) -> None:
        path = Path(self.id().replace(".", "_") + ".txt")
        try:
            path.write_text("dosya icerigi", encoding="utf-8")
            result = extract_text_from_path(path, max_chars=1000)
            self.assertTrue(result["ok"])
            self.assertIn("dosya", result["text"])
            self.assertEqual(result["path"], str(path.resolve()))
        finally:
            if path.exists():
                path.unlink()

    def test_html_keeps_visible_text_and_drops_script_and_style(self) -> None:
        markup = (
            "<html><head><title>Ders</title><style>p{color:red}</style>"
            "<SCRIPT type='text/javascript'>var gizli = '<p>x</p>';</SCRIPT></head>"
            "<body><h1>Hafta&nbsp;1</h1><p>Ödev&amp;Proje<br/>son <b>gün</b>: 12 Mart</p>"
            "<script>alert(1)</script><!-- yorum --><p>bitti</p></body></html>"
        ).encode("utf-8")
        result = extract_text_from_bytes(markup, extension=".html")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "Ders Hafta 1 Ödev&Proje son gün : 12 Mart bitti")
        for hidden in ("gizli", "alert", "color", "yorum"):
            self.assertNotIn(hidden, result["text"])
        self.assertEqual(extract_text_from_bytes(b"a<br>b<scr", extension=".htm")["text"], "a b")

    def test_unclosed_script_tags_scale_linearly(self) -> None:
        """The old regular expression was quadratic here: 200 KiB took seconds."""

        def seconds(payload: bytes) -> float:
            best = float("inf")
            for _ in range(3):
                started = time.perf_counter()
                result = extract_text_from_bytes(payload, extension=".html")
                best = min(best, time.perf_counter() - started)
                self.assertTrue(result["ok"])
            return best

        for unit in (b"<script ", b"<script>", b"<style>", b"<script>x</script", b"<", b"<!--"):
            with self.subTest(unit=unit):
                small = seconds(unit * (256 * 1024 // len(unit)))
                large = seconds(unit * (2048 * 1024 // len(unit)))
                # Eight times the input. Linear work is about 8x; quadratic would be 64x.
                self.assertLess(large, max(small, 0.005) * 24)
                self.assertLess(large, 20)

    def test_oversized_input_is_refused_with_a_reason(self) -> None:
        with patch("ninova_mcp.text_extract.MAX_EXTRACT_BYTES", 64):
            result = extract_text_from_bytes(b"x" * 65, extension=".txt")
            self.assertFalse(result["ok"])
            self.assertIn("extraction limit", result["error"])
            self.assertTrue(extract_text_from_bytes(b"x" * 64, extension=".txt")["ok"])
            with tempfile.TemporaryDirectory(prefix="itu-extract-") as directory:
                path = Path(directory) / "big.txt"
                path.write_bytes(b"x" * 65)
                from_path = extract_text_from_path(path)
        self.assertFalse(from_path["ok"])
        self.assertEqual(from_path["size_bytes"], 65)
        self.assertIn("extraction limit", from_path["error"])

    def test_pdf_page_cap_is_reported_not_silent(self) -> None:
        from pypdf import PdfWriter

        from ninova_mcp.text_extract import MAX_PDF_PAGES

        def pdf(pages: int) -> bytes:
            writer = PdfWriter()
            for _ in range(pages):
                writer.add_blank_page(width=72, height=72)
            buffer = io.BytesIO()
            writer.write(buffer)
            return buffer.getvalue()

        self.assertEqual(MAX_PDF_PAGES, 50)
        short = extract_text_from_bytes(pdf(3), extension=".pdf")
        self.assertTrue(short["ok"], short.get("error"))
        self.assertEqual((short["page_count"], short["pages_read"], short["truncated"]), (3, 3, False))
        self.assertNotIn("pages_truncated", short)
        long = extract_text_from_bytes(pdf(MAX_PDF_PAGES + 7), extension=".pdf")
        self.assertTrue(long["ok"], long.get("error"))
        self.assertEqual((long["page_count"], long["pages_read"]), (57, 50))
        self.assertTrue(long["truncated"])
        self.assertTrue(long["pages_truncated"])
        self.assertIn("first 50 of 57 pages", long["truncation_note"])


if __name__ == "__main__":
    unittest.main()
