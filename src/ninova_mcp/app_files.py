"""Ninova page and file tools: read, crawl, download, text extraction and snapshots."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import NinovaClient, NinovaError
from .parsing import (
    SnapshotReference,
    compare_snapshot_payloads,
    is_internal_ninova_url,
    make_snapshot_payload,
    normalize_url,
    parse_html_page,
    sanitize_filename,
    slugify,
)
from .text_extract import (
    DEFAULT_MAX_CHARS as TEXT_EXTRACT_DEFAULT_MAX_CHARS,
    extract_text_from_bytes,
    extract_text_from_path,
    guess_extension,
)


class PageFileToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies ``client`` and the state paths.

    The course URL helpers come from ``NinovaCourseToolsMixin``.
    """

    client: NinovaClient
    state_dir: Path
    snapshot_dir: Path

    def read_page(
        self,
        url: str,
        include_text: bool = True,
        link_limit: int = 200,
        compact: bool = False,
    ) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return {
                "url": response.url,
                "content_type": content_type,
                "content_length": response.headers.get("Content-Length"),
                "message": (
                    "The requested resource is not HTML. "
                    "Use download_resource or read_resource_text."
                ),
            }

        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        result = {
            "url": page_data["url"],
            "title": page_data["title"],
            "headings": page_data["headings"],
            "links": page_data["links"][: max(1, min(link_limit, 500))],
            "attachments": page_data["attachments"],
            "tables": page_data["tables"],
            "text_hash": page_data["text_hash"],
        }
        if include_text:
            result["text_excerpt"] = page_data["text_excerpt"]
        return self._out(result, compact=compact)

    def crawl_course(
        self,
        course_url: str,
        max_depth: int = 2,
        max_pages: int = 25,
        include_downloads: bool = True,
    ) -> dict[str, Any]:
        start_url = normalize_url(course_url, self.client.base_url)
        course_path = self._extract_course_root_path(start_url)
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(start_url, 0)]
        pages: list[dict[str, Any]] = []
        downloads: list[dict[str, Any]] = []
        seen_downloads: set[tuple[str, str]] = set()

        from .interaction import progress_steps

        # The number of pages is not known in advance, so no total is reported.
        steps = progress_steps()
        while queue and len(pages) < max_pages:
            current_url, depth = queue.pop(0)
            normalized = self._strip_fragment(current_url)
            if normalized in visited:
                continue
            visited.add(normalized)
            steps.advance(f"Sayfa taranıyor ({len(pages) + 1}, en fazla {max_pages}): {normalized}")

            html, response = self.client.get_html(normalized)
            page = parse_html_page(response.url, html, base_url=self.client.base_url)
            page_summary = {
                "url": page["url"],
                "title": page["title"],
                "headings": page["headings"][:10],
                "link_count": len(page["links"]),
                "attachment_count": len(page["attachments"]),
            }
            pages.append(page_summary)

            if include_downloads:
                for attachment in page["attachments"]:
                    key = (attachment["url"], attachment["text"])
                    if key in seen_downloads:
                        continue
                    seen_downloads.add(key)
                    downloads.append(attachment)

            if depth >= max_depth:
                continue

            for link in page["links"]:
                if link["kind"] != "page":
                    continue
                if not is_internal_ninova_url(link["url"], self.client.base_url):
                    continue
                if not self._is_inside_course(link["url"], course_path):
                    continue
                candidate = self._strip_fragment(link["url"])
                if candidate not in visited:
                    queue.append((candidate, depth + 1))

        return {
            "course_url": start_url,
            "course_path": course_path,
            "pages_crawled": len(pages),
            "pages": pages,
            "downloads": downloads[:500],
        }

    def download_resource(
        self,
        url: str,
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.get(url, stream=True)
        target_dir = Path(output_dir or (self.state_dir / "downloads")).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        resolved_name = sanitize_filename(filename) if filename else self._filename_from_response(response)
        target_path = self._unique_download_path(target_dir / resolved_name)
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)

        return {
            "url": response.url,
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "path": str(target_path),
        }

    def read_resource_text(
        self,
        url: str | None = None,
        path: str | None = None,
        max_chars: int = TEXT_EXTRACT_DEFAULT_MAX_CHARS,
        filename: str | None = None,
        save_download: bool = False,
    ) -> dict[str, Any]:
        """Download (or open) a Ninova file and return extracted plain text for the LLM.

        Provide either ``url`` (authenticated Ninova resource) or a local ``path``
        previously saved via ``download_resource``.
        """
        if not url and not path:
            raise NinovaError("Provide either url or path.")
        if url and path:
            raise NinovaError("Provide only one of url or path, not both.")

        max_chars = max(1_000, min(int(max_chars), 200_000))

        if path:
            extracted = extract_text_from_path(path, max_chars=max_chars)
            extracted["source"] = "path"
            return extracted

        if url is None:  # narrowed above; keep runtime safety under python -O
            raise NinovaError("Provide a Ninova resource URL.")
        response = self.client.get(url, stream=True)
        content_type = response.headers.get("Content-Type")
        resolved_name = sanitize_filename(filename) if filename else self._filename_from_response(response)
        extension = guess_extension(
            url=response.url,
            content_type=content_type,
            filename=resolved_name,
        )

        chunks: list[bytes] = []
        total = 0
        max_bytes = 25 * 1024 * 1024  # 25 MiB safety cap for in-memory extract
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise NinovaError(
                    f"Resource larger than {max_bytes} bytes; download with "
                    "download_resource and pass path= to read_resource_text."
                )
            chunks.append(chunk)
        data = b"".join(chunks)

        saved_path: str | None = None
        if save_download:
            target_dir = (self.state_dir / "downloads").expanduser().resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = self._unique_download_path(target_dir / resolved_name)
            target_path.write_bytes(data)
            saved_path = str(target_path)

        extracted = extract_text_from_bytes(data, extension=extension, max_chars=max_chars)
        extracted.update(
            {
                "source": "url",
                "url": response.url,
                "content_type": content_type,
                "filename": resolved_name,
                "size_bytes": len(data),
            }
        )
        if saved_path:
            extracted["path"] = saved_path
        return extracted

    def snapshot_page(self, url: str, label: str | None = None) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        payload = make_snapshot_payload(page_data, label=label)
        snapshot_path = self._snapshot_path(page_data["url"], label=label)
        # Write-then-rename: a crash mid-write must not leave a truncated
        # file that a later diff_snapshot scan would trip over.
        tmp_path = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(
                {
                    "captured_at": datetime.now(tz=UTC).isoformat(),
                    "snapshot": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(snapshot_path)
        return {
            "snapshot_path": str(snapshot_path),
            "url": page_data["url"],
            "title": page_data["title"],
            "text_hash": page_data["text_hash"],
        }

    def diff_snapshot(
        self,
        url: str,
        snapshot_path: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        current = make_snapshot_payload(page_data, label=label)
        previous = self._load_snapshot(url=response.url, snapshot_path=snapshot_path, label=label)
        comparison = compare_snapshot_payloads(previous.payload, current)
        comparison["snapshot_path"] = str(previous.path)
        return comparison

    def _load_snapshot(
        self,
        *,
        url: str,
        snapshot_path: str | None,
        label: str | None,
    ) -> SnapshotReference:
        if snapshot_path:
            path = Path(snapshot_path).expanduser().resolve()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))["snapshot"]
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                raise NinovaError(f"Snapshot file is unreadable or corrupt: {path}") from exc
            return SnapshotReference(path=path, payload=payload)

        candidates = sorted(self.snapshot_dir.glob("*.json"), reverse=True)
        normalized_url = normalize_url(url, self.client.base_url)
        for candidate in candidates:
            try:
                document = json.loads(candidate.read_text(encoding="utf-8"))
                snapshot = document["snapshot"]
            except (OSError, json.JSONDecodeError, KeyError):
                # A corrupt snapshot from an interrupted write shouldn't stop
                # the scan from finding a good one among the rest.
                continue
            if snapshot.get("url") != normalized_url:
                continue
            if label is not None and snapshot.get("label") != label:
                continue
            return SnapshotReference(path=candidate, payload=snapshot)
        raise NinovaError("No matching snapshot was found.")

    def _snapshot_path(self, url: str, label: str | None) -> Path:
        parsed = urlparse(url)
        slug = slugify((label or parsed.path.strip("/") or "dashboard").replace("/", "-"))
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        return self.snapshot_dir / f"{slug}-{timestamp}.json"

    def _is_inside_course(self, url: str, course_path: str) -> bool:
        path = urlparse(url).path
        return path == course_path or path.startswith(course_path + "/")

    def _filename_from_response(self, response: Any) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        filename_match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
        if filename_match:
            return sanitize_filename(filename_match.group(1))
        filename_match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        if filename_match:
            return sanitize_filename(filename_match.group(1))
        parsed = urlparse(response.url)
        basename = Path(parsed.path).name
        return sanitize_filename(basename or "download")

    def _unique_download_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem or "download"
        suffix = path.suffix
        counter = 2
        while True:
            candidate = path.with_name(f"{stem}-{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1
