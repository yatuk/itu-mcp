from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ninova_mcp.parsing import (
    extension_allowed,
    extract_assignment_upload_form,
    match_upload_slot,
)
from ninova_mcp.server import NinovaMcpApp

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "odev_gonder.html"
BASE = "https://ninova.itu.edu.tr"
UPLOAD_URL = f"{BASE}/Sinif/32945.119022/Odev/245598/OdevGonder"


class UploadParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = FIXTURE.read_text(encoding="utf-8")

    def test_extract_form_and_slots(self) -> None:
        form = extract_assignment_upload_form(self.html, UPLOAD_URL, BASE)
        self.assertTrue(form["ok"])
        self.assertEqual(form["submit_event_target"], "ctl00$ContentPlaceHolder1$lbEkle")
        self.assertEqual(form["requested_file_count"], 5)
        self.assertEqual(form["uploaded_file_count"], 4)
        self.assertTrue(all(slot.get("field_name") for slot in form["slots"]))
        empty = [s for s in form["slots"] if not s["uploaded"]]
        self.assertEqual(len(empty), 1)
        self.assertIn("Abroad", empty[0]["description"])

    def test_match_slot(self) -> None:
        form = extract_assignment_upload_form(self.html, UPLOAD_URL, BASE)
        by_index = match_upload_slot(form["slots"], slot_index=5)
        by_desc = match_upload_slot(form["slots"], slot_description="Abroad Petition")
        self.assertEqual(by_index["field_name"], by_desc["field_name"])

    def test_extension_allowed(self) -> None:
        self.assertTrue(extension_allowed("a.pdf", [".pdf"]))
        self.assertFalse(extension_allowed("a.docx", [".pdf"]))
        self.assertTrue(extension_allowed("a.zip", [".zip", ".rar"]))


class SubmitAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name)
        self.file_path = self.state_dir / "abroad.pdf"
        self.file_path.write_bytes(b"%PDF-1.4 test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _app(self) -> NinovaMcpApp:
        with patch.dict(
            "os.environ",
            {
                "NINOVA_USERNAME": "dummy",
                "NINOVA_PASSWORD": "dummy",
                "NINOVA_STATE_DIR": str(self.state_dir),
                "NINOVA_ALLOW_UPLOADS": "1",
            },
            clear=False,
        ):
            return NinovaMcpApp()

    def test_dry_run_requires_confirm(self) -> None:
        app = self._app()
        mock_response = MagicMock()
        mock_response.url = UPLOAD_URL
        with patch.object(app, "_resolve_assignment_upload_url", return_value={
            "upload_url": UPLOAD_URL,
            "course": {"code": "STJ", "url": f"{BASE}/Sinif/1.2"},
            "assignment": {"title": "Internship", "url": f"{BASE}/Sinif/1.2/Odev/1"},
        }), patch.object(app.client, "get_html", return_value=(self.html, mock_response)):
            result = app.submit_assignment(
                file_path=str(self.file_path),
                upload_url=UPLOAD_URL,
                slot_index=5,
                confirm=False,
            )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["slot"]["index"], 5)
        self.assertIn("confirm=true", result["message"])

    def test_blocks_replace_without_flag(self) -> None:
        app = self._app()
        mock_response = MagicMock()
        mock_response.url = UPLOAD_URL
        with patch.object(app, "_resolve_assignment_upload_url", return_value={
            "upload_url": UPLOAD_URL,
            "course": None,
            "assignment": None,
        }), patch.object(app.client, "get_html", return_value=(self.html, mock_response)):
            with self.assertRaises(Exception) as raised:
                app.submit_assignment(
                    file_path=str(self.file_path),
                    upload_url=UPLOAD_URL,
                    slot_index=1,
                    confirm=True,
                    allow_replace=False,
                )
        self.assertIn("already has an uploaded file", str(raised.exception))

    def test_confirm_posts_multipart(self) -> None:
        app = self._app()
        mock_get = MagicMock()
        mock_get.url = UPLOAD_URL
        # After upload, mark slot 5 as uploaded.
        after_html = self.html.replace(
            "Abroad Petition and Other Documents (Passport, Insurance,...etc) Dosyayı henüz g",
            "Abroad Petition and Other Documents (Passport, Insurance,...etc) Dosyayı 10 Temmuz 2026 12:00 tarihinde sisteme yüklediniz.",
        )
        # Ensure the Turkish empty marker path is flipped if present.
        after_html = after_html.replace("henüz göndermediniz", "yüklediniz")
        after_html = after_html.replace("henuz gondermediniz", "yuklediniz")
        mock_post = MagicMock()
        mock_post.url = UPLOAD_URL
        mock_post.headers = {"Content-Type": "text/html"}
        mock_post.text = after_html
        mock_post.encoding = "utf-8"
        mock_post.apparent_encoding = "utf-8"

        with patch.object(app, "_resolve_assignment_upload_url", return_value={
            "upload_url": UPLOAD_URL,
            "course": None,
            "assignment": {"title": "X"},
        }), patch.object(app.client, "get_html", return_value=(self.html, mock_get)), patch.object(
            app.client, "post_multipart", return_value=mock_post
        ) as post, patch.object(
            app.client, "_decode_response", return_value=after_html
        ), patch.object(
            app.client, "_looks_like_login_page", return_value=False
        ):
            result = app.submit_assignment(
                file_path=str(self.file_path),
                upload_url=UPLOAD_URL,
                slot_description="Abroad",
                confirm=True,
            )

        self.assertFalse(result["dry_run"])
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["data"]["__EVENTTARGET"], "ctl00$ContentPlaceHolder1$lbEkle")
        self.assertEqual(kwargs["files"][0][0], "ctl00$ContentPlaceHolder1$gvOdevDosyaTipleri$ctl06$fuUpload")
        self.assertEqual(kwargs["files"][0][1][0], "abroad.pdf")


if __name__ == "__main__":
    unittest.main()
