from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ninova_mcp import interaction
from ninova_mcp.client import NinovaError
from ninova_mcp.parsing import (
    extension_allowed,
    extract_assignment_upload_form,
    match_upload_slot,
)
from ninova_mcp.server import NinovaMcpApp

from recording import RecordingAdapter, offline_app

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
        self.state_dir = Path(self.temp.name) / "state"
        self.state_dir.mkdir()
        self.file_path = Path(self.temp.name) / "abroad.pdf"
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


class UploadTargetTests(unittest.TestCase):
    """Where the file is really posted, and that the user approves those exact bytes."""

    ORIGINAL = b"%PDF-1.4 the version the user approved"
    FORM_ACTION = 'action="/Sinif/32945.119022/Odev/245598/OdevGonder"'

    def setUp(self) -> None:
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.assertIn(self.FORM_ACTION, self.html)
        self.files = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="itu-upload-")))
        self.file_path = self.files / "abroad.pdf"
        self.file_path.write_bytes(self.ORIGINAL)
        self.prompts: list[str] = []

    def app(self, html: str | None = None, script: dict[str, Any] | None = None) -> tuple[Any, RecordingAdapter]:
        page = (200, {"Content-Type": "text/html; charset=utf-8"}, (html or self.html).encode("utf-8"))
        return offline_app(self, {UPLOAD_URL: page, **(script or {})})

    def submit(self, app: Any, *, answer: str = "accept", on_ask: Any = None, **overrides: Any) -> dict[str, Any]:
        def ask(message: str) -> str:
            self.prompts.append(message)
            if on_ask is not None:
                on_ask()
            return answer

        arguments = {"file_path": str(self.file_path), "upload_url": UPLOAD_URL, "slot_index": 5, "confirm": True}
        arguments.update(overrides)
        with interaction.bind(interaction.ToolInteraction(elicitation_supported=True, ask=ask)):
            return app.submit_assignment(**arguments)

    def posts(self, adapter: RecordingAdapter) -> list[Any]:
        return [item for item in adapter.requests if item.method == "POST"]

    def test_page_on_another_host_is_refused_without_a_request(self) -> None:
        app, adapter = self.app()
        path = "/Sinif/32945.119022/Odev/245598/OdevGonder"
        for url in (
            "https://yardim.itu.edu.tr" + path, "https://obs.itu.edu.tr" + path, "https://portal.itu.edu.tr" + path,
            "https://x.itu.edu.tr" + path, "https://attacker.example" + path,
            "https://attacker.example\\@ninova.itu.edu.tr" + path, "http://ninova.itu.edu.tr" + path,
            "https://ninova.itu.edu.tr:8443" + path,
        ):
            with self.subTest(url=url):
                with self.assertRaises(NinovaError):
                    self.submit(app, upload_url=url)
                with self.assertRaises(NinovaError):
                    app.get_assignment_upload_slots(upload_url=url)
        self.assertEqual(adapter.hosts, [])
        self.assertEqual(self.prompts, [])

    def test_path_that_is_not_an_upload_page_is_refused_without_a_request(self) -> None:
        app, adapter = self.app()
        for url in (
            "/Kampus1", "/Sinif/32945.119022", "/Sinif/32945.119022/Odevler", "/Sinif/32945.119022/Duyuru/1",
            "/Sinif/32945.119022/Odev/245598/OdevGonder/extra", "/Sinif/32945.119022/Odev/245598/Baska",
            "/Sinif/32945.119022/Odev/245598/OdevGonder/../../../../Kampus1", "/x/Sinif/32945.119022/Odev/245598/OdevGonder",
            "/Sinif/abc/Odev/245598/OdevGonder", BASE + "/Login.aspx?ReturnUrl=/Sinif/1.2/Odev/3/OdevGonder",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(NinovaError, "not a Ninova assignment upload page"):
                    self.submit(app, upload_url=url)
                with self.assertRaisesRegex(NinovaError, "not a Ninova assignment upload page"):
                    app.get_assignment_upload_slots(upload_url=url)
        self.assertEqual(adapter.hosts, [])
        self.assertEqual(self.prompts, [])

    def test_assignment_detail_url_still_resolves_to_the_upload_page(self) -> None:
        app, adapter = self.app()
        for url in ("/Sinif/32945.119022/Odev/245598", BASE + "/Sinif/32945.119022/Odev/245598/", UPLOAD_URL, UPLOAD_URL + "/"):
            with self.subTest(url=url):
                resolved = app._resolve_assignment_upload_url(course=None, assignment=None, upload_url=url)
                self.assertEqual(resolved["upload_url"].rstrip("/"), UPLOAD_URL)
        slots = app.get_assignment_upload_slots(upload_url="/Sinif/32945.119022/Odev/245598")
        self.assertTrue(slots["ok"])
        self.assertEqual(adapter.urls, [UPLOAD_URL])

    def test_upload_link_read_from_a_page_is_checked_too(self) -> None:
        app, adapter = self.app()
        course = {"code": "STJ", "url": BASE + "/Sinif/32945.119022"}
        for link in ("https://portal.itu.edu.tr/collect", BASE + "/Kampus1", "https://attacker.example/Sinif/1.2/Odev/3/OdevGonder"):
            with self.subTest(link=link):
                listing = {"assignments": [{"title": "Internship", "url": BASE + "/Sinif/32945.119022/Odev/245598", "upload_url": link}]}
                with patch.object(app, "_resolve_course", return_value=course), \
                        patch.object(app, "get_course_assignments", return_value=listing):
                    with self.assertRaises(NinovaError):
                        self.submit(app, upload_url=None, course="STJ", assignment="Internship")
        self.assertEqual(adapter.hosts, [])
        self.assertEqual(self.prompts, [])

    def test_upload_page_that_redirects_elsewhere_is_refused(self) -> None:
        app, adapter = self.app(script={UPLOAD_URL: (302, {"Location": BASE + "/Kampus1"}, b"")})
        with self.assertRaisesRegex(NinovaError, "not a Ninova assignment upload page"):
            self.submit(app)
        self.assertEqual(self.posts(adapter), [])
        self.assertEqual(self.prompts, [])

    def test_foreign_form_action_is_refused_before_asking_or_posting(self) -> None:
        for action in (
            "https://portal.itu.edu.tr/collect",
            "https://yardim.itu.edu.tr/Sinif/32945.119022/Odev/245598/OdevGonder",
            "https://attacker.example/Sinif/32945.119022/Odev/245598/OdevGonder",
            "//attacker.example/Sinif/32945.119022/Odev/245598/OdevGonder",
            "https://attacker.example\\@ninova.itu.edu.tr/Sinif/32945.119022/Odev/245598/OdevGonder",
            "http://ninova.itu.edu.tr/Sinif/32945.119022/Odev/245598/OdevGonder",
            "/Kampus1",
            "/Sinif/32945.119022/Odevler",
            "/Sinif/32945.119022/Odev/999999/OdevGonder",
            "/Sinif/1.2/Odev/245598/OdevGonder",
            "javascript:alert(1)",
        ):
            for confirm in (True, False):
                with self.subTest(action=action, confirm=confirm):
                    app, adapter = self.app(self.html.replace(self.FORM_ACTION, f'action="{action}"'))
                    with self.assertRaises(NinovaError):
                        self.submit(app, confirm=confirm)
                    self.assertEqual(self.posts(adapter), [])
                    self.assertEqual(set(adapter.hosts), {"ninova.itu.edu.tr"})
        self.assertEqual(self.prompts, [])

    def test_relative_form_actions_resolve_to_the_same_upload_page(self) -> None:
        for action in ("OdevGonder", "./OdevGonder", "", "/Sinif/32945.119022/Odev/245598/OdevGonder?x=1", UPLOAD_URL):
            with self.subTest(action=action):
                app, _ = self.app(self.html.replace(self.FORM_ACTION, f'action="{action}"'))
                preview = self.submit(app, confirm=False)
                self.assertTrue(preview["dry_run"])
                self.assertTrue(preview["form_url"].startswith(UPLOAD_URL))

    def test_confirmation_shows_the_post_target_size_and_hash(self) -> None:
        action = "/Sinif/32945.119022/Odev/245598/OdevGonder?step=2"
        app, adapter = self.app(self.html.replace(self.FORM_ACTION, f'action="{action}"'), script={
            UPLOAD_URL + "?step=2": (200, {"Content-Type": "text/html"}, self.html.encode("utf-8")),
        })
        digest = hashlib.sha256(self.ORIGINAL).hexdigest()
        preview = self.submit(app, confirm=False)
        self.assertEqual(preview["file"]["sha256"], digest)
        self.assertEqual(preview["form_url"], UPLOAD_URL + "?step=2")
        self.assertEqual(self.prompts, [])

        self.submit(app)
        lines = self.prompts[0].splitlines()
        self.assertIn(f"Yükleme adresi: {UPLOAD_URL}", lines)
        self.assertIn(f"Dosyanın gönderileceği adres (POST): {UPLOAD_URL}?step=2", lines)
        self.assertIn(f"Dosya: abroad.pdf, {len(self.ORIGINAL)} bayt", lines)
        self.assertTrue(any(digest[:16] in line and "SHA-256" in line for line in lines))
        self.assertNotIn(digest[:17], self.prompts[0])
        self.assertEqual([str(item.url) for item in self.posts(adapter)], [UPLOAD_URL + "?step=2"])

    def test_bytes_sent_are_the_bytes_approved_even_if_the_file_changes(self) -> None:
        app, adapter = self.app()
        replacement = b"%PDF-1.4 something else entirely, swapped in after the read"

        def swap() -> None:
            self.assertEqual(self.posts(adapter), [])
            self.file_path.write_bytes(replacement)

        result = self.submit(app, on_ask=swap)
        self.assertEqual(self.file_path.read_bytes(), replacement)
        posts = self.posts(adapter)
        self.assertEqual(len(posts), 1)
        body = posts[0].body if isinstance(posts[0].body, bytes) else posts[0].body.encode()
        self.assertIn(self.ORIGINAL, body)
        self.assertNotIn(replacement, body)
        digest = hashlib.sha256(self.ORIGINAL).hexdigest()
        self.assertIn(digest[:16], self.prompts[0])
        self.assertEqual(result["file"]["sha256"], digest)
        self.assertEqual(result["file"]["size_bytes"], len(self.ORIGINAL))

    def test_declined_dialog_sends_nothing(self) -> None:
        app, adapter = self.app()
        result = self.submit(app, answer="decline")
        self.assertEqual(result["status"], "declined_by_user")
        self.assertEqual(self.posts(adapter), [])

    def test_post_is_never_replayed_to_another_host(self) -> None:
        for target in ("https://portal.itu.edu.tr/collect", "https://attacker.example/collect"):
            with self.subTest(target=target):
                page = (200, {"Content-Type": "text/html"}, self.html.encode("utf-8"))

                def reply(request: Any, target: str = target) -> Any:
                    return (307, {"Location": target}, b"") if request.method == "POST" else page

                app, adapter = self.app(script={UPLOAD_URL: reply})
                with self.assertRaises(NinovaError):
                    self.submit(app)
                self.assertEqual(set(adapter.hosts), {"ninova.itu.edu.tr"})

    def test_existing_guards_still_run_first(self) -> None:
        app, adapter = self.app()
        with patch.dict("os.environ", {"NINOVA_ALLOW_UPLOADS": "0"}):
            with self.assertRaisesRegex(NinovaError, "disabled"):
                self.submit(app)
        with self.assertRaisesRegex(NinovaError, "already has an uploaded file"):
            self.submit(app, slot_index=1)
        empty = self.files / "empty.pdf"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(NinovaError, "empty"):
            self.submit(app, file_path=str(empty))
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.posts(adapter), [])


if __name__ == "__main__":
    unittest.main()
