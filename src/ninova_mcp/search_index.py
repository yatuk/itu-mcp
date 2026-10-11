"""Local full-text index over course content the tools have already fetched.

One SQLite file in the state directory. ``documents`` keeps the original
text; ``docs_folded`` keeps the same rows folded for matching (an FTS5 table
when SQLite has FTS5, a plain table scanned with ``LIKE`` otherwise).

Turkish folding is done here, not by SQLite: ``unicode61`` does not fold the
dotless ``ı`` to ``i``. The fold is a 1:1 character table, so the folded text
has the length of the original and an offset found in one is valid in the
other. ``str.lower()`` is never used on the text: it turns ``İ`` into two
code points and would shift every offset after it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .private_io import PRIVATE_FILE_MODE, ensure_private_dir

SEARCH_INDEX_FILENAME = "search.sqlite3"
KINDS = ("announcement", "assignment", "message_topic", "file_name", "file_text")
MAX_LIMIT = 25
MAX_SNIPPET_CHARS = 240
MAX_QUERY_TOKENS = 12
MAX_TOKEN_CHARS = 64
PREFIX_MIN_CHARS = 4
# One document (a page, an announcement) never stores more than this.
MAX_DOCUMENT_CHARS = 200_000
# Total stored text, counted in characters of the original. The oldest
# documents are evicted above it.
MAX_TOTAL_CHARS = 200 * 1024 * 1024

_FOLD_PAIRS = {
    "ı": "i", "İ": "i", "I": "i",
    "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
}
_FOLD_TABLE = {ord(char): target for char, target in _FOLD_PAIRS.items()}
_FOLD_TABLE.update({code: chr(code + 32) for code in range(ord("A"), ord("Z") + 1) if chr(code) != "I"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    course_url TEXT,
    course_code TEXT,
    course_title TEXT,
    title TEXT NOT NULL,
    url TEXT,
    date TEXT,
    page INTEGER,
    source_key TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_course ON documents(course_url);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_fts5_available: bool | None = None


def fold(text: str) -> str:
    """Fold Turkish letters and ASCII capitals, one character for one character."""
    return text.translate(_FOLD_TABLE)


def fold_key(text: Any) -> str:
    """Folded text with everything but letters and digits removed (``BLG 335E`` -> ``blg335e``)."""
    return "".join(char for char in fold(str(text or "")) if char.isalnum())


def normalize_text(text: Any) -> str:
    """NFC text without NUL characters: composed letters are what the fold table knows."""
    return unicodedata.normalize("NFC", str(text or "")).replace("\x00", " ")


def query_tokens(query: Any) -> list[str]:
    """Folded letter/digit runs of the query. Operators and quotes never survive this."""
    folded = fold(normalize_text(query))
    tokens: list[str] = []
    for token in re.split(r"[\W_]+", folded):
        if token and token not in tokens:
            tokens.append(token[:MAX_TOKEN_CHARS])
    return tokens[:MAX_QUERY_TOKENS]


def match_expression(tokens: list[str], *, any_token: bool = False) -> str:
    """FTS5 query: every token quoted, a prefix star on tokens of four or more characters."""
    parts = [
        '"' + token.replace('"', "") + '"' + ("*" if len(token) >= PREFIX_MIN_CHARS else "")
        for token in tokens
    ]
    return (" OR " if any_token else " AND ").join(parts)


def make_snippet(body: str, tokens: list[str], *, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    """Cut the original text around the first match found in its folded form."""
    folded = fold(body)
    position = -1
    for token in tokens:
        match = re.search(r"(?<![0-9a-z])" + re.escape(token), folded)
        found = match.start() if match else folded.find(token)
        if found >= 0 and (position < 0 or found < position):
            position = found
    budget = max(20, max_chars - 2)
    start = 0 if position < 0 else max(0, position - budget // 3)
    piece = body[start:start + budget]
    text = " ".join(piece.split())
    if start > 0:
        text = "…" + text
    if start + budget < len(body):
        text += "…"
    return text[:max_chars]


def fts5_available() -> bool:
    """Probe FTS5 once per process."""
    global _fts5_available
    if _fts5_available is None:
        try:
            with contextlib.closing(sqlite3.connect(":memory:")) as probe:
                probe.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
            _fts5_available = True
        except sqlite3.Error:
            _fts5_available = False
    return _fts5_available


def _content_hash(document: dict[str, Any]) -> str:
    parts = [str(document.get(key) or "") for key in (
        "kind", "course_url", "course_code", "course_title", "title", "url", "date", "page", "body",
    )]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


class SearchIndex:
    """The index file. A connection is opened per operation and closed after it."""

    def __init__(
        self,
        path: str | Path,
        *,
        force_scan: bool = False,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ) -> None:
        self.path = Path(path)
        self.engine = "scan" if force_scan or not fts5_available() else "fts5"
        self.max_total_chars = max_total_chars

    # -- connection -----------------------------------------------------

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        ensure_private_dir(self.path.parent)
        if not self.path.exists():
            # Created owner-only before SQLite opens it; SQLite gives its
            # journal the permissions of the database file.
            flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
            os.close(os.open(self.path, flags, PRIVATE_FILE_MODE))
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.create_function("fold_key", 1, fold_key, deterministic=True)
            self._prepare(connection)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _prepare(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_SCHEMA)
        row = connection.execute("SELECT value FROM meta WHERE key = 'engine'").fetchone()
        if row is not None and row["value"] == self.engine:
            return
        # First use, or the file was written with the other engine: rebuild
        # the folded table from the stored originals.
        connection.execute("DROP TABLE IF EXISTS docs_folded")
        if self.engine == "fts5":
            connection.execute(
                "CREATE VIRTUAL TABLE docs_folded USING fts5(title, body, tokenize = 'unicode61 remove_diacritics 0')"
            )
        else:
            connection.execute("CREATE TABLE docs_folded (title TEXT NOT NULL, body TEXT NOT NULL)")
        for item in connection.execute("SELECT id, title, body FROM documents").fetchall():
            connection.execute(
                "INSERT INTO docs_folded(rowid, title, body) VALUES (?, ?, ?)",
                (item["id"], fold(item["title"]), fold(item["body"])),
            )
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('engine', ?)", (self.engine,))

    # -- writing --------------------------------------------------------

    @staticmethod
    def _delete_ids(connection: sqlite3.Connection, ids: Iterable[int]) -> int:
        count = 0
        for doc_id in ids:
            connection.execute("DELETE FROM docs_folded WHERE rowid = ?", (doc_id,))
            connection.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            count += 1
        return count

    def _write(self, connection: sqlite3.Connection, documents: Iterable[dict[str, Any]]) -> tuple[dict[str, int], set[str]]:
        counts = {"indexed": 0, "unchanged": 0}
        seen: set[str] = set()
        for raw in documents:
            if raw.get("kind") not in KINDS or not raw.get("source_key"):
                continue
            document = dict(raw)
            document["title"] = normalize_text(document.get("title")).strip() or "(adsız)"
            document["body"] = normalize_text(document.get("body")).strip()[:MAX_DOCUMENT_CHARS]
            key = str(document["source_key"])
            if key in seen:
                continue
            seen.add(key)
            digest = _content_hash(document)
            existing = connection.execute(
                "SELECT id, content_hash FROM documents WHERE source_key = ?", (key,)
            ).fetchone()
            if existing is not None and existing["content_hash"] == digest:
                counts["unchanged"] += 1
                continue
            if existing is not None:
                self._delete_ids(connection, [existing["id"]])
            cursor = connection.execute(
                "INSERT INTO documents(kind, course_url, course_code, course_title, title, url, date, page,"
                " source_key, content_hash, body, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document["kind"], document.get("course_url"), document.get("course_code"),
                    document.get("course_title"), document["title"], document.get("url"),
                    document.get("date"), document.get("page"), key, digest, document["body"], _now(),
                ),
            )
            connection.execute(
                "INSERT INTO docs_folded(rowid, title, body) VALUES (?, ?, ?)",
                (cursor.lastrowid, fold(document["title"]), fold(document["body"])),
            )
            counts["indexed"] += 1
        return counts, seen

    def _evict(self, connection: sqlite3.Connection) -> int:
        total = connection.execute("SELECT COALESCE(SUM(LENGTH(body) + LENGTH(title)), 0) FROM documents").fetchone()[0]
        if total <= self.max_total_chars:
            return 0
        doomed: list[int] = []
        for row in connection.execute(
            "SELECT id, LENGTH(body) + LENGTH(title) AS size FROM documents ORDER BY indexed_at, id"
        ):
            if total <= self.max_total_chars:
                break
            doomed.append(row["id"])
            total -= row["size"]
        return self._delete_ids(connection, doomed)

    def upsert(self, documents: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Add or update documents; one whose ``source_key`` and content are unchanged is skipped."""
        with self._connect() as connection:
            counts, _ = self._write(connection, documents)
            counts["deleted"] = self._evict(connection)
        return counts

    def replace_prefix(self, prefix: str, documents: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Upsert ``documents`` and delete every other document whose key starts with ``prefix``."""
        with self._connect() as connection:
            counts, seen = self._write(connection, documents)
            stale = [
                row["id"] for row in connection.execute(
                    "SELECT id, source_key FROM documents WHERE substr(source_key, 1, ?) = ?", (len(prefix), prefix)
                ).fetchall() if row["source_key"] not in seen
            ]
            counts["deleted"] = self._delete_ids(connection, stale) + self._evict(connection)
        return counts

    def prune_courses(self, tracked_course_urls: Iterable[str]) -> int:
        """Delete the documents of courses that are no longer tracked."""
        tracked = set(tracked_course_urls)
        with self._connect() as connection:
            stale = [
                row["id"] for row in connection.execute(
                    "SELECT id, course_url FROM documents WHERE course_url IS NOT NULL"
                ).fetchall() if row["course_url"] not in tracked
            ]
            return self._delete_ids(connection, stale)

    def clear(self) -> int:
        """Delete every document and return how many there were."""
        if not self.path.exists():
            return 0
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            connection.execute("DELETE FROM docs_folded")
            connection.execute("DELETE FROM documents")
        with contextlib.closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("VACUUM")
        return int(count)

    # -- reading --------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"doc_count": 0, "last_indexed_at": None}
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n, MAX(indexed_at) AS last FROM documents").fetchone()
        return {"doc_count": int(row["n"]), "last_indexed_at": row["last"]}

    def _filters(self, course: Any, kinds: list[str] | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        key = fold_key(course) if course is not None else ""
        if key:
            clauses.append("(fold_key(d.course_code) = ? OR instr(fold_key(d.course_title), ?) > 0)")
            params += [key, key]
        if kinds:
            clauses.append("d.kind IN (" + ", ".join("?" for _ in kinds) + ")")
            params += list(kinds)
        return "".join(" AND " + clause for clause in clauses), params

    def _run_fts(self, connection, tokens, any_token, where, params, limit, offset):
        expression = match_expression(tokens, any_token=any_token)
        base = "FROM docs_folded JOIN documents d ON d.id = docs_folded.rowid WHERE docs_folded MATCH ?" + where
        total = connection.execute("SELECT COUNT(*) " + base, [expression, *params]).fetchone()[0]
        rows = connection.execute(
            "SELECT d.*, -bm25(docs_folded, 4.0, 1.0) AS score " + base + " ORDER BY score DESC, d.id LIMIT ? OFFSET ?",
            [expression, *params, limit, offset],
        ).fetchall()
        return int(total), rows

    def _run_scan(self, connection, tokens, any_token, where, params, limit, offset):
        patterns = [f"%{token}%" for token in tokens]
        each = "(f.title LIKE ? OR f.body LIKE ?)"
        condition = "(" + (" OR " if any_token else " AND ").join(each for _ in tokens) + ")"
        score = " + ".join("(f.title LIKE ?) * 2 + (f.body LIKE ?)" for _ in tokens)
        doubled = [pattern for pattern in patterns for _ in (0, 1)]
        base = "FROM docs_folded f JOIN documents d ON d.id = f.rowid WHERE " + condition + where
        total = connection.execute("SELECT COUNT(*) " + base, [*doubled, *params]).fetchone()[0]
        rows = connection.execute(
            f"SELECT d.*, ({score}) AS score " + base + " ORDER BY score DESC, d.id LIMIT ? OFFSET ?",
            [*doubled, *doubled, *params, limit, offset],
        ).fetchall()
        return int(total), rows

    def search(
        self,
        query: Any,
        *,
        course: Any = None,
        kinds: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the index. Never raises on the query text, whatever it contains."""
        limit = max(1, min(int(limit), MAX_LIMIT))
        offset = max(0, int(offset))
        tokens = query_tokens(query)
        stats = self.stats()
        result: dict[str, Any] = {
            "status": "ok", "engine": self.engine, "matched": "all",
            "total_count": 0, "count": 0, "next_offset": None, "index": stats, "results": [],
        }
        if stats["doc_count"] == 0:
            result["status"] = "empty_index"
            return result
        if not tokens:
            result["status"] = "empty_query"
            return result

        where, params = self._filters(course, kinds)
        engine = self.engine
        total, rows = 0, []
        with self._connect() as connection:
            for any_token in (False, True):
                if any_token and len(tokens) < 2:
                    break
                try:
                    runner = self._run_fts if engine == "fts5" else self._run_scan
                    total, rows = runner(connection, tokens, any_token, where, params, limit, offset)
                except sqlite3.Error:
                    if engine != "fts5":
                        raise
                    # An expression FTS5 refuses is answered by a scan of the same table.
                    engine = "scan"
                    total, rows = self._run_scan(connection, tokens, any_token, where, params, limit, offset)
                if total:
                    result["matched"] = "any" if any_token else "all"
                    break
        result["engine"] = engine
        result["total_count"] = total
        result["count"] = len(rows)
        if offset + len(rows) < total:
            result["next_offset"] = offset + len(rows)
        if not total:
            result["status"] = "no_match"
        for row in rows:
            result["results"].append({
                "kind": row["kind"],
                "course": {"code": row["course_code"], "title": row["course_title"]},
                "title": row["title"],
                "snippet": make_snippet(row["body"] or row["title"], tokens),
                "page": row["page"],
                "date": row["date"],
                "url": row["url"],
                "score": round(float(row["score"] or 0), 3),
            })
        return result
