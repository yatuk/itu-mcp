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


def _pptx(slides: dict[int, str], *, notes: dict[int, str] | None = None, media: int = 0,
          extra_parts: dict[str, bytes] | None = None) -> bytes:
    """Build a tiny PPTX (zip of DrawingML parts); ``slides`` maps slide number to its paragraphs XML."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number, body in slides.items():
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                f'<p:sld xmlns:a="a" xmlns:p="p"><p:txBody>{body}</p:txBody></p:sld>',
            )
            if notes and number in notes:
                archive.writestr(
                    f"ppt/slides/_rels/slide{number}.xml.rels",
                    f'<Relationships><Relationship Target="../notesSlides/notesSlide{number + 10}.xml"/></Relationships>',
                )
                archive.writestr(
                    f"ppt/notesSlides/notesSlide{number + 10}.xml",
                    f"<p:notes><a:p><a:r><a:t>{notes[number]}</a:t></a:r></a:p></p:notes>",
                )
        for index in range(media):
            archive.writestr(f"ppt/media/image{index}.png", b"png")
        for name, content in (extra_parts or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _para(*runs: str) -> str:
    return "<a:p>" + "".join(f"<a:r><a:t>{run}</a:t></a:r>" for run in runs) + "</a:p>"


class PptxExtractionTests(unittest.TestCase):
    def test_slide_text_in_slide_order_not_zip_order(self) -> None:
        data = _pptx({2: _para("Second"), 1: _para("First")})
        result = extract_text_from_bytes(data, extension=".pptx")
        self.assertTrue(result["ok"])
        self.assertLess(result["text"].index("First"), result["text"].index("Second"))
        self.assertIn("--- slide 1 ---", result["text"])
        self.assertEqual(result["slide_count"], 2)

    def test_runs_in_one_paragraph_join_and_paragraphs_split(self) -> None:
        data = _pptx({1: _para("Midterm: ", "10 Nov") + _para("Final: 5 Jan")})
        text = extract_text_from_bytes(data, extension=".pptx")["text"]
        self.assertIn("Midterm: 10 Nov\nFinal: 5 Jan", text)

    def test_entities_are_decoded(self) -> None:
        data = _pptx({1: _para("Q&amp;A &lt;draft&gt; caf&#233;")})
        self.assertIn("Q&A <draft> café", extract_text_from_bytes(data, extension=".pptx")["text"])

    def test_speaker_notes_are_attached_to_their_slide(self) -> None:
        data = _pptx({1: _para("Syllabus")}, notes={1: "Exam dates are in the notes"})
        text = extract_text_from_bytes(data, extension=".pptx")["text"]
        self.assertIn("[notes] Exam dates are in the notes", text)

    def test_entity_definitions_are_not_expanded(self) -> None:
        """No XML parser runs, so a DOCTYPE entity bomb or external entity does nothing."""
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "AAAA"><!ENTITY b "&a;&a;&a;&a;">'
            '<!ENTITY ext SYSTEM "file:///etc/passwd">]>'
            "<p:sld><a:p><a:r><a:t>&b;&ext;</a:t></a:r></a:p></p:sld>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", bomb)
        text = extract_text_from_bytes(buffer.getvalue(), extension=".pptx")["text"]
        self.assertNotIn("AAAA", text)
        self.assertNotIn("root:", text)

    def test_oversized_part_is_skipped_and_reported(self) -> None:
        with patch("ninova_mcp.text_extract.MAX_PPTX_PART_BYTES", 200):
            data = _pptx({1: _para("small"), 2: _para("x" * 500)})
            result = extract_text_from_bytes(data, extension=".pptx")
        self.assertIn("small", result["text"])
        self.assertNotIn("xxxx", result["text"])
        self.assertTrue(result["truncated"])
        self.assertIn("size safety limit", result["truncation_note"])

    def test_slide_count_cap_is_reported(self) -> None:
        with patch("ninova_mcp.text_extract.MAX_PPTX_SLIDES", 2):
            data = _pptx({n: _para(f"s{n}") for n in range(1, 5)})
            result = extract_text_from_bytes(data, extension=".pptx")
        self.assertIn("first 2 of 4 slides", result["truncation_note"])
        self.assertNotIn("s3", result["text"])

    def test_image_only_deck_explains_why_it_is_empty(self) -> None:
        data = _pptx({1: "<a:p></a:p>"}, media=3)
        result = extract_text_from_bytes(data, extension=".pptx")
        self.assertTrue(result["ok"])
        self.assertTrue(result["empty"])
        self.assertIn("3 images", result["image_only_hint"])

    def test_pptx_detected_from_content_without_extension(self) -> None:
        result = extract_text_from_bytes(_pptx({1: _para("Hello")}), extension="")
        self.assertTrue(result["ok"])
        self.assertEqual(result["extension"], ".pptx")
        self.assertIn("Hello", result["text"])

    def test_content_type_maps_to_pptx(self) -> None:
        ctype = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        self.assertEqual(guess_extension(content_type=ctype), ".pptx")

    def test_legacy_ppt_gets_a_specific_hint(self) -> None:
        legacy = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64
        result = extract_text_from_bytes(legacy, extension=".ppt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["extension"], ".ppt")
        self.assertIn("PowerPoint", result["error"])

    def test_corrupt_pptx_returns_error_not_exception(self) -> None:
        result = extract_text_from_bytes(b"PK\x03\x04 not really a zip", extension=".pptx")
        self.assertFalse(result["ok"])

    def test_pages_out_gets_one_entry_per_slide(self) -> None:
        pages: list = []
        extract_text_from_bytes(_pptx({1: _para("A"), 2: _para("B")}), extension=".pptx", pages_out=pages)
        self.assertEqual([page for page, _ in pages], [1, 2])


if __name__ == "__main__":
    unittest.main()
