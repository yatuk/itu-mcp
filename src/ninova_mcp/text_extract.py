"""Extract plain text from downloaded or in-memory office/PDF files."""

from __future__ import annotations

import io
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm"}
DEFAULT_MAX_CHARS = 50_000
# Largest file read into memory for extraction, for a URL and for a local path alike.
MAX_EXTRACT_BYTES = 25 * 1024 * 1024
# Same page limit as the mail attachment reader. Pages beyond it are not read
# and the result says so.
MAX_PDF_PAGES = 50


class _VisibleTextParser(HTMLParser):
    """Collect the text a reader would see, skipping script and style content.

    ``HTMLParser`` scans its input once, so an unclosed ``<script`` costs time
    in proportion to the input. The regular expression this replaces went
    back over the rest of the text for every unclosed tag.
    """

    _HIDDEN_TAGS = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in self._HIDDEN_TAGS:
            self._hidden_depth += 1
        elif self._hidden_depth == 0:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs
        if self._hidden_depth == 0:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._HIDDEN_TAGS:
            if self._hidden_depth:
                self._hidden_depth -= 1
        elif self._hidden_depth == 0:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def _html_to_text(markup: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(markup)
    parser.close()
    return parser.text()


def _extension_from_name(name: str | None) -> str:
    if not name:
        return ""
    return Path(name).suffix.lower()


def guess_extension(
    *,
    path: str | Path | None = None,
    url: str | None = None,
    content_type: str | None = None,
    filename: str | None = None,
) -> str:
    for candidate in (filename, str(path) if path else None):
        ext = _extension_from_name(candidate)
        if ext:
            return ext
    if url:
        ext = _extension_from_name(urlparse(url).path)
        if ext:
            return ext
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/csv": ".csv",
        "application/json": ".json",
        "text/html": ".html",
        "application/xml": ".xml",
        "text/xml": ".xml",
    }
    return mapping.get(ctype, "")


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _extract_pdf(data: bytes) -> tuple[str, dict[str, Any]]:
    """Return the text of at most ``MAX_PDF_PAGES`` pages and what was skipped."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise RuntimeError(
            "PDF extraction requires pypdf. Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)
    parts: list[str] = []
    for index in range(1, min(page_count, MAX_PDF_PAGES) + 1):
        page = reader.pages[index - 1]
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        page_text = page_text.strip()
        if page_text:
            parts.append(f"--- page {index} ---\n{page_text}")
    details: dict[str, Any] = {"page_count": page_count, "pages_read": min(page_count, MAX_PDF_PAGES)}
    if page_count > MAX_PDF_PAGES:
        details["pages_truncated"] = True
        details["truncation_note"] = (
            f"Only the first {MAX_PDF_PAGES} of {page_count} pages were read (page safety limit)."
        )
    return "\n\n".join(parts).strip(), details


def _extract_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "DOCX extraction requires python-docx. Install with: pip install python-docx"
        ) from exc

    document = Document(io.BytesIO(data))
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
    # Tables often hold assignment rubrics / schedules.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    return "\n".join(paragraphs).strip()


def _extract_plain(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1254", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_LEGACY_OFFICE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_ZIP_MAGIC = bytes.fromhex("504b0304")
LEGACY_DOC_HINT = (
    "This is a legacy Word (.doc) file, which cannot be read here. Next step: get the file as "
    ".docx or PDF (open it in Word or LibreOffice and save it as .docx or PDF, or ask the "
    "instructor for a PDF) and read that; download_resource still saves the original for the user."
)


def sniff_extension(data: bytes) -> str:
    """Name the file type from its first bytes: ``.pdf``, ``.docx``, ``.doc`` or ``""``."""
    if data[:1024].lstrip().startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith(_LEGACY_OFFICE_MAGIC):
        return ".doc"
    if data.startswith(_ZIP_MAGIC):
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, OSError, ValueError):
            return ""
        if any(name.startswith("word/") for name in names):
            return ".docx"
    return ""


def extract_text_from_bytes(
    data: bytes,
    *,
    extension: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}" if extension else ""
    details: dict[str, Any] = {}
    if ext not in SUPPORTED_EXTENSIONS and ext not in {".htm"}:
        # A file saved without a usable suffix is still what its bytes say it is.
        sniffed = sniff_extension(data) if len(data) <= MAX_EXTRACT_BYTES else ""
        if sniffed in SUPPORTED_EXTENSIONS:
            ext = sniffed
            details["extension_source"] = "content"
        else:
            legacy = ext == ".doc" or sniffed == ".doc"
            return {
                "ok": False,
                "extension": ".doc" if legacy else ext or None,
                "error": LEGACY_DOC_HINT if legacy else (
                    f"Unsupported file type {ext or '(unknown)'}. "
                    f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                ),
                "text": None,
                "char_count": 0,
                "truncated": False,
            }

    if len(data) > MAX_EXTRACT_BYTES:
        return {
            "ok": False,
            "extension": ext,
            "error": (
                f"File is larger than the {MAX_EXTRACT_BYTES // (1024 * 1024)} MiB text extraction limit "
                f"({len(data)} bytes)."
            ),
            "text": None,
            "char_count": 0,
            "truncated": False,
        }
    try:
        if ext == ".pdf":
            text, pdf_details = _extract_pdf(data)
            details.update(pdf_details)
        elif ext == ".docx":
            text = _extract_docx(data)
        else:
            text = _extract_plain(data)
            if ext in {".html", ".htm"}:
                text = _html_to_text(text)
    except Exception as exc:
        return {
            "ok": False,
            "extension": ext,
            "error": str(exc),
            "text": None,
            "char_count": 0,
            "truncated": False,
            **details,
        }

    text = (text or "").strip()
    truncated_text, truncated = _truncate(text, max_chars)
    return {
        "ok": True,
        "extension": ext,
        "error": None,
        "text": truncated_text,
        "char_count": len(text),
        "returned_chars": len(truncated_text),
        "truncated": truncated or bool(details.get("pages_truncated")),
        "empty": not bool(text),
        **details,
    }


def extract_text_from_path(
    path: str | Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    extension: str | None = None,
) -> dict[str, Any]:
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        return {
            "ok": False,
            "path": str(file_path),
            "error": f"File not found: {file_path}",
            "text": None,
            "char_count": 0,
            "truncated": False,
        }
    size = file_path.stat().st_size
    if size > MAX_EXTRACT_BYTES:
        return {
            "ok": False,
            "path": str(file_path),
            "size_bytes": size,
            "error": (
                f"File is larger than the {MAX_EXTRACT_BYTES // (1024 * 1024)} MiB text extraction limit "
                f"({size} bytes)."
            ),
            "text": None,
            "char_count": 0,
            "truncated": False,
        }
    with file_path.open("rb") as handle:
        # Bounded read: the file may grow between the size check and here.
        data = handle.read(MAX_EXTRACT_BYTES + 1)
    ext = extension or guess_extension(path=file_path)
    result = extract_text_from_bytes(data, extension=ext, max_chars=max_chars)
    result["path"] = str(file_path)
    result["size_bytes"] = len(data)
    return result
