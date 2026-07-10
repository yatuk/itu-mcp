from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
