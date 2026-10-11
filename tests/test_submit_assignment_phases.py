"""Order of the steps of ``submit_assignment`` (characterization, offline).

Each test pins where one step stands relative to the requests, to the
confirmation dialog and to the POST, so a guard that moves fails here.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from ninova_mcp import interaction
from ninova_mcp.client import NinovaAuthError, NinovaError
from ninova_mcp.parsing import extract_assignment_upload_form, match_upload_slot
from ninova_mcp.server import NinovaMcpApp

from recording import RecordingAdapter, offline_app

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "odev_gonder.html"
BASE = "https://ninova.itu.edu.tr"
UPLOAD_PATH = "/Sinif/32945.119022/Odev/245598/OdevGonder"
UPLOAD_URL = BASE + UPLOAD_PATH
FORM_ACTION = f'action="{UPLOAD_PATH}"'
SUBMIT_TARGET = "ctl00$ContentPlaceHolder1$lbEkle"
EMPTY_SLOT_FIELD = "ctl00$ContentPlaceHolder1$gvOdevDosyaTipleri$ctl06$fuUpload"
CONTENT = b"%PDF-1.4 the bytes that were read before anything was asked"
DIGEST = hashlib.sha256(CONTENT).hexdigest()

PREVIEW_KEYS = ["dry_run", "course", "assignment", "upload_url", "form_url", "submit_label", "slot", "file"]
REFUSED_KEYS = [*PREVIEW_KEYS, "ok", "uploaded", "status", "message", "confirmation"]
RESULT_KEYS = [
    "dry_run", "ok", "message", "course", "assignment", "upload_url", "response_url",
    "slot_before", "slot_after", "file", "requested_file_count", "uploaded_file_count",
]
DRY_RUN_MESSAGE = (
    "Dry run only. Re-call submit_assignment with the same arguments "
    "and confirm=true to actually upload this file to Ninova."
)
NOT_SUCCESSFUL = "Upload POST completed but the slot still looks empty; check Ninova in a browser."
ANY_FILE_SLOT = {"index": 1, "description": "Any", "field_name": "f1", "uploaded": False, "allowed_extensions": []}


class UploadFlowCase(unittest.TestCase):
    def setUp(self) -> None:
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.assertIn(FORM_ACTION, self.html)
        self.form = extract_assignment_upload_form(self.html, UPLOAD_URL, BASE)
        self.files = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="itu-phases-"))).resolve()
        self.file_path = self.files / "abroad.pdf"
        self.file_path.write_bytes(CONTENT)
        self.events: list[Any] = []
        self.prompt = ""

    def app(self, html: str | None = None, after_html: str | None = None) -> tuple[Any, RecordingAdapter]:
        page = (html or self.html).encode("utf-8")
        after = (after_html or html or self.html).encode("utf-8")

        def reply(request: Any) -> Any:
            self.events.append((request.method, str(request.url)))
            return (200, {"Content-Type": "text/html; charset=utf-8"}, after if request.method == "POST" else page)

        app, adapter = offline_app(self, {UPLOAD_URL: reply})
        os.environ["NINOVA_ALLOW_UPLOADS"] = "1"
        os.environ.pop(interaction.REQUIRE_ELICITATION_ENV, None)
        return app, adapter

    def submit(self, app: Any, *, answer: Any = "accept", **overrides: Any) -> dict[str, Any]:
        def ask(message: str) -> str:
            self.events.append("ask")
            self.prompt = message
            if isinstance(answer, Exception):
                raise answer
            return answer() if callable(answer) else answer

        arguments = {"file_path": str(self.file_path), "upload_url": UPLOAD_URL, "slot_index": 5, "confirm": True}
        arguments.update(overrides)
        with interaction.bind(interaction.ToolInteraction(elicitation_supported=True, ask=ask)):
            return app.submit_assignment(**arguments)

    def direct(self, app: Any, path: Path | None = None, **overrides: Any) -> dict[str, Any]:
        """A plain method call: no MCP request, so no dialog is available."""
        arguments = {"upload_url": UPLOAD_URL, "slot_index": 5, "confirm": True}
        arguments.update(overrides)
        return app.submit_assignment(str(path or self.file_path), **arguments)

    def refused(self, app: Any, message: str, **overrides: Any) -> None:
        with self.assertRaises(NinovaError) as caught:
            self.submit(app, **overrides)
        self.assertEqual(str(caught.exception), message)

    def posted_page(self, app: Any, html: str, url: str = UPLOAD_URL) -> Any:
        """Replace the POST and the reading of its answer; returns the POST mock."""
        posted = MagicMock()
        posted.url = url
        post = self.enterContext(patch.object(app.client, "post_multipart", return_value=posted))
        decode, login_check = app.client._decode_response, app.client._looks_like_login_page
        self.posted_is_login_page = False
        # Reading the upload page goes through the same two client methods, so only the POST's answer is replaced.
        self.decode = self.enterContext(patch.object(
            app.client, "_decode_response", side_effect=lambda response: html if response is posted else decode(response),
        ))
        self.login_check = self.enterContext(patch.object(
            app.client, "_looks_like_login_page",
            side_effect=lambda response, **kwargs: (
                self.posted_is_login_page if response is posted else login_check(response, **kwargs)
            ),
        ))
        return post


class LocalChecksComeFirstTests(UploadFlowCase):
    """Nothing is requested, resolved or asked before the local file is accepted."""

    def assert_refused_locally(self, app: Any, adapter: RecordingAdapter, message: str, **overrides: Any) -> None:
        with patch.object(app, "_resolve_assignment_upload_url", side_effect=AssertionError("resolved too early")):
            self.refused(app, message, **overrides)
        self.assertEqual(adapter.requests, [])
        self.assertEqual(self.events, [])

    def test_switch_is_checked_before_the_file_and_the_target(self) -> None:
        app, adapter = self.app()
        message = "Assignment uploads are disabled (NINOVA_ALLOW_UPLOADS=0)."
        for value in ("0", "false", "NO", " off "):
            with self.subTest(value=value), patch.dict(os.environ, {"NINOVA_ALLOW_UPLOADS": value}):
                self.assert_refused_locally(app, adapter, message)
                self.assert_refused_locally(app, adapter, message, file_path="\\\\server\\share\\a.pdf")
                self.assert_refused_locally(app, adapter, message, upload_url="https://attacker.example/x")
        with patch.dict(os.environ, {"NINOVA_ALLOW_UPLOADS": "2"}):
            self.assertTrue(self.submit(app, confirm=False)["dry_run"])

    def test_refused_paths_stop_before_any_request(self) -> None:
        app, adapter = self.app()
        secret = app.state_dir / "session.json"
        secret.write_text("{}", encoding="utf-8")
        cases = {
            "\\\\server\\share\\a.pdf": "file_path must not be a network (UNC) or device path.",
            "//server/share/a.pdf": "file_path must not be a network (UNC) or device path.",
            "": "file_path is empty or contains a NUL character.",
            "a\x00.pdf": "file_path is empty or contains a NUL character.",
            str(secret): "file_path is inside the server's state directory, which is never uploaded.",
        }
        for path, message in cases.items():
            with self.subTest(path=path):
                self.assert_refused_locally(app, adapter, message, file_path=path)
                # The file is judged before the target is.
                self.assert_refused_locally(app, adapter, message, file_path=path, upload_url="https://attacker.example/x")

    def test_missing_empty_and_oversized_files_stop_before_any_request(self) -> None:
        app, adapter = self.app()
        missing = self.files / "missing.pdf"
        self.assert_refused_locally(app, adapter, f"File not found: {missing}", file_path=str(missing))
        self.assert_refused_locally(app, adapter, f"File not found: {self.files}", file_path=str(self.files))
        empty = self.files / "empty.pdf"
        empty.write_bytes(b"")
        self.assert_refused_locally(app, adapter, f"File is empty: {empty}", file_path=str(empty))
        with patch("ninova_mcp.app_ninova.MAX_UPLOAD_BYTES", len(CONTENT) - 1):
            self.assert_refused_locally(
                app, adapter, f"File too large ({len(CONTENT)} bytes). Max is {len(CONTENT) - 1} bytes.",
            )
        with patch("ninova_mcp.app_ninova.MAX_UPLOAD_BYTES", len(CONTENT)):
            self.assertTrue(self.submit(app, confirm=False)["dry_run"])

    def test_size_is_judged_again_on_the_bytes_that_were_read(self) -> None:
        app, adapter = self.app()

        class ChangingFile:
            """Reports one size and then yields other content, like a file that changes in between."""

            name = "abroad.pdf"

            def __init__(self, content: bytes) -> None:
                self.content = content
                self.read_sizes: list[Any] = []

            def is_file(self) -> bool:
                return True

            def stat(self) -> Any:
                return SimpleNamespace(st_size=5)

            def open(self, mode: str) -> Any:
                assert mode == "rb"
                owner = self

                class Stream(io.BytesIO):
                    def read(self, size: int | None = -1) -> bytes:
                        owner.read_sizes.append(size)
                        return super().read(size)

                return Stream(self.content)

            def __str__(self) -> str:
                return "fake/abroad.pdf"

        grown = ChangingFile(b"x" * 40)
        with patch("ninova_mcp.app_ninova.MAX_UPLOAD_BYTES", 10), \
                patch("ninova_mcp.app_ninova.resolve_upload_file", return_value=grown) as resolve:
            self.assert_refused_locally(app, adapter, "File too large (more than 10 bytes). Max is 10 bytes.")
        self.assertEqual(grown.read_sizes, [11])
        self.assertEqual(resolve.call_args.args, (str(self.file_path),))
        self.assertEqual(resolve.call_args.kwargs, {"state_dir": app.state_dir})
        vanished = ChangingFile(b"")
        with patch("ninova_mcp.app_ninova.resolve_upload_file", return_value=vanished):
            self.assert_refused_locally(app, adapter, "File is empty: fake/abroad.pdf")

    def test_local_refusal_does_not_even_build_the_client(self) -> None:
        state = self.files / "state"
        environment = {"NINOVA_STATE_DIR": str(state), "NINOVA_ALLOW_UPLOADS": "1"}
        with patch.dict(os.environ, environment), patch("ninova_mcp.server.load_ninova_env"), \
                patch("ninova_mcp.server.NinovaClient") as client_class:
            app = NinovaMcpApp()
            for path in ("\\\\server\\share\\a.pdf", str(self.files / "missing.pdf"), str(state / "x.json")):
                with self.subTest(path=path), self.assertRaises(NinovaError):
                    app.submit_assignment(file_path=path, upload_url=UPLOAD_URL, confirm=True)
            with patch.dict(os.environ, {"NINOVA_ALLOW_UPLOADS": "0"}), self.assertRaises(NinovaError):
                app.submit_assignment(file_path=str(self.file_path), upload_url=UPLOAD_URL, confirm=True)
            client_class.assert_not_called()

    def test_file_is_read_before_the_target_is_resolved(self) -> None:
        app, adapter = self.app()
        resolve = app._resolve_assignment_upload_url

        def swap_then_resolve(**kwargs: Any) -> dict[str, Any]:
            self.assertEqual(adapter.requests, [])
            self.file_path.write_bytes(b"swapped while the target was being resolved")
            self.events.append("resolve")
            return resolve(**kwargs)

        with patch.object(app, "_resolve_assignment_upload_url", side_effect=swap_then_resolve) as resolver:
            result = self.submit(app)
        self.assertEqual(resolver.call_args.kwargs, {"course": None, "assignment": None, "upload_url": UPLOAD_URL})
        self.assertEqual(self.events, ["resolve", ("GET", UPLOAD_URL), "ask", ("POST", UPLOAD_URL)])
        self.assertEqual(result["file"], {
            "path": str(self.file_path), "name": "abroad.pdf", "size_bytes": len(CONTENT), "sha256": DIGEST,
        })
        body = adapter.requests[-1].body
        self.assertIn(CONTENT, body)
        self.assertNotIn(b"swapped", body)


class PageChecksTests(UploadFlowCase):
    """The upload page is read once; every page check precedes the dialog and the POST."""

    def assert_page_refused(self, html: str | None, message: str, **overrides: Any) -> None:
        for confirm in (True, False):
            with self.subTest(confirm=confirm, message=message):
                self.events.clear()
                app, _ = self.app(html)
                self.refused(app, message, confirm=confirm, **overrides)
                self.assertEqual(self.events, [("GET", UPLOAD_URL)])

    def matcher_message(self, **kwargs: Any) -> str:
        with self.assertRaises(ValueError) as caught:
            match_upload_slot(self.form["slots"], **kwargs)
        return str(caught.exception)

    def test_unreadable_form_is_refused(self) -> None:
        self.assert_page_refused("<html><body><p>Bakım</p></body></html>", "No HTML form found on the upload page.")

    def test_form_without_a_submit_control_is_refused_before_its_target_is_judged(self) -> None:
        message = "Could not find the assignment submit control (Ödevi Gönder)."
        html = f'<html><body><form {FORM_ACTION}><input type="file" name="f"></form></body></html>'
        self.assert_page_refused(html, message)
        self.assert_page_refused(html.replace(FORM_ACTION, 'action="/Sinif/1.2/Odev/3/OdevGonder"'), message)

    def test_form_target_is_judged_before_the_slot(self) -> None:
        other = self.html.replace(FORM_ACTION, 'action="/Sinif/32945.119022/Odev/999999/OdevGonder"')
        different = (
            "The upload form's target belongs to a different assignment than the upload page. Nothing was sent."
        )
        self.assert_page_refused(other, different)
        self.assert_page_refused(other, different, slot_index=99)
        elsewhere = self.html.replace(FORM_ACTION, 'action="/Kampus1"')
        not_upload = (
            "The upload form's target is not a Ninova assignment upload page "
            "(/Sinif/<id>.<id>/Odev/<id>/OdevGonder). Nothing was sent."
        )
        self.assert_page_refused(elsewhere, not_upload, slot_index=99)

    def test_redirected_upload_page_is_refused_before_its_form_is_read(self) -> None:
        app, adapter = self.app()
        adapter.script[UPLOAD_URL] = (302, {"Location": BASE + "/Kampus1"}, b"")
        adapter.script[BASE + "/Kampus1"] = (200, {"Content-Type": "text/html"}, self.html.encode("utf-8"))
        with patch("ninova_mcp.app_ninova.extract_assignment_upload_form") as parse:
            self.refused(app, (
                "The upload page is not a Ninova assignment upload page "
                "(/Sinif/<id>.<id>/Odev/<id>/OdevGonder). Nothing was sent."
            ))
        parse.assert_not_called()
        self.assertEqual(adapter.urls, [UPLOAD_URL, BASE + "/Kampus1"])
        self.assertEqual(self.events, [])

    def test_slot_errors_keep_their_order(self) -> None:
        self.assert_page_refused(None, self.matcher_message(slot_index=99, slot_description=None), slot_index=99)
        slot = {
            "index": 9, "description": "Report", "field_name": None, "uploaded": True,
            "allowed_extensions": [".zip"], "extensions": "*.zip",
        }
        with patch("ninova_mcp.server.match_upload_slot", return_value=slot) as match:
            self.assert_page_refused(None, "Slot 9 has no file input field (Report).")
            self.assertEqual(match.call_args.args, (self.form["slots"],))
            self.assertEqual(match.call_args.kwargs, {"slot_index": 5, "slot_description": None})
            slot["field_name"] = "f9"
            self.assert_page_refused(
                None,
                "Slot 9 (Report) already has an uploaded file. "
                "Pass allow_replace=true to overwrite, or pick another slot.",
            )
            self.assert_page_refused(
                None, "File extension of 'abroad.pdf' is not allowed for this slot. Allowed: *.zip", allow_replace=True,
            )
            slot["extensions"] = None
            self.assert_page_refused(
                None, "File extension of 'abroad.pdf' is not allowed for this slot. Allowed: any", allow_replace=True,
            )

    def test_slot_description_reaches_the_matcher(self) -> None:
        app, _ = self.app()
        preview = self.submit(app, slot_index=None, slot_description="Abroad", confirm=False)
        self.assertEqual(preview["slot"]["index"], 5)
        message = self.matcher_message(slot_index=None, slot_description="no such slot")
        self.refused(app, message, slot_index=None, slot_description="no such slot")
        self.assertNotIn("ask", self.events)


class PreviewAndConfirmationTests(UploadFlowCase):
    def expected_preview(self, *, dry_run: bool) -> dict[str, Any]:
        slot = self.form["slots"][4]
        return {
            "dry_run": dry_run,
            "course": None,
            "assignment": {"url": UPLOAD_URL, "title": None},
            "upload_url": UPLOAD_URL,
            "form_url": UPLOAD_URL,
            "submit_label": self.form["submit_label"],
            "slot": {
                "index": 5, "description": slot["description"], "extensions": slot["extensions"],
                "field_name": EMPTY_SLOT_FIELD, "already_uploaded": False,
            },
            "file": {"path": str(self.file_path), "name": "abroad.pdf", "size_bytes": len(CONTENT), "sha256": DIGEST},
        }

    def test_dry_run_returns_the_preview_without_asking_or_posting(self) -> None:
        app, _ = self.app()
        preview = self.submit(app, confirm=False)
        self.assertEqual(list(preview), [*PREVIEW_KEYS, "message"])
        self.assertEqual(list(preview["slot"]), ["index", "description", "extensions", "field_name", "already_uploaded"])
        self.assertEqual(list(preview["file"]), ["path", "name", "size_bytes", "sha256"])
        self.assertEqual(preview, {**self.expected_preview(dry_run=True), "message": DRY_RUN_MESSAGE})
        self.assertEqual(self.events, [("GET", UPLOAD_URL)])

    def test_confirm_defaults_to_a_dry_run(self) -> None:
        app, _ = self.app()
        preview = app.submit_assignment(str(self.file_path), upload_url=UPLOAD_URL, slot_index=5)
        self.assertIs(preview["dry_run"], True)
        self.assertEqual(self.events, [("GET", UPLOAD_URL)])

    def test_every_answer_but_accept_posts_nothing(self) -> None:
        answers = {
            "decline": ("declined_by_user", "decline"),
            "cancel": ("cancelled_by_user", "cancel"),
            "anything else": ("confirmation_unavailable", None),
        }
        for answer, (status, action) in answers.items():
            with self.subTest(answer=answer):
                self.events.clear()
                app, _ = self.app()
                result = self.submit(app, answer=answer)
                self.assertEqual(self.events, [("GET", UPLOAD_URL), "ask"])
                self.assertEqual(list(result), REFUSED_KEYS)
                self.assertEqual({key: result[key] for key in PREVIEW_KEYS}, self.expected_preview(dry_run=False))
                self.assertIs(result["ok"], False)
                self.assertIs(result["uploaded"], False)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["confirmation"]["user_action"], action)
                self.assertEqual(result["confirmation"]["method"], "elicitation")
                self.assertEqual(result["message"], result["confirmation"]["note"])
                self.assertNotIn("proceed", result["confirmation"])
                self.assertNotIn("status", result["confirmation"])

    def test_dialog_that_fails_posts_nothing(self) -> None:
        app, _ = self.app()
        result = self.submit(app, answer=RuntimeError("client went away"))
        self.assertEqual(result["status"], "confirmation_unavailable")
        self.assertEqual(self.events, [("GET", UPLOAD_URL), "ask"])

    def test_required_elicitation_without_a_dialog_posts_nothing(self) -> None:
        app, _ = self.app()
        with patch.dict(os.environ, {interaction.REQUIRE_ELICITATION_ENV: "1"}):
            result = self.direct(app)
        self.assertEqual(list(result), REFUSED_KEYS)
        self.assertEqual(result["status"], "elicitation_required")
        self.assertEqual(self.events, [("GET", UPLOAD_URL)])

    def test_dialog_names_the_file_that_was_read_and_the_post_target(self) -> None:
        app, _ = self.app()
        answer = {"proceed": False, "status": "declined_by_user", "note": "n", "method": "elicitation"}
        with patch("ninova_mcp.interaction.upload_confirmation_message", return_value="soru") as build, \
                patch("ninova_mcp.interaction.confirm_write", return_value=answer) as gate:
            result = self.direct(app)
        self.assertEqual(build.call_args.args, (
            None, {"url": UPLOAD_URL, "title": None}, UPLOAD_URL, self.form["slots"][4],
            "abroad.pdf", str(self.file_path), len(CONTENT),
        ))
        self.assertEqual(build.call_args.kwargs, {"post_url": UPLOAD_URL, "sha256_hex": DIGEST})
        gate.assert_called_once_with("soru")
        self.assertEqual(result["confirmation"], {"note": "n", "method": "elicitation"})
        self.assertEqual(self.events, [("GET", UPLOAD_URL)])

    def test_only_exactly_true_proceeds(self) -> None:
        app, _ = self.app()
        for proceed in (1, "yes", None):
            answer = {"proceed": proceed, "status": "s", "note": "n"}
            with self.subTest(proceed=proceed), patch("ninova_mcp.interaction.confirm_write", return_value=answer):
                self.assertEqual(self.direct(app)["status"], "s")
        self.assertEqual(self.events, [("GET", UPLOAD_URL)] * 3)

    def test_dialog_is_not_built_for_a_dry_run(self) -> None:
        app, _ = self.app()
        with patch("ninova_mcp.interaction.upload_confirmation_message") as build, \
                patch("ninova_mcp.interaction.confirm_write") as gate:
            self.direct(app, confirm=False)
        build.assert_not_called()
        gate.assert_not_called()


class PostAndResultTests(UploadFlowCase):
    def test_accepted_upload_is_one_get_one_dialog_one_post(self) -> None:
        uploaded = self.html.replace("henüz göndermediniz", "10 Temmuz 2026 12:00 tarihinde sisteme yüklediniz")
        self.assertNotEqual(uploaded, self.html)
        app, adapter = self.app(after_html=uploaded)

        def accept_after_checking() -> str:
            self.assertEqual(self.events, [("GET", UPLOAD_URL), "ask"])
            self.file_path.write_bytes(b"swapped while the dialog was open")
            return "accept"

        result = self.submit(app, answer=accept_after_checking)
        self.assertEqual(self.events, [("GET", UPLOAD_URL), "ask", ("POST", UPLOAD_URL)])
        post = adapter.requests[-1]
        self.assertEqual(post.headers["Referer"], UPLOAD_URL)
        self.assertIn(CONTENT, post.body)
        self.assertNotIn(b"swapped", post.body)
        self.assertIn(DIGEST[:16], self.prompt)

        self.assertEqual(list(result), ["confirmation", *RESULT_KEYS])
        self.assertEqual(result["confirmation"]["method"], "elicitation")
        self.assertIs(result["dry_run"], False)
        self.assertIs(result["ok"], True)
        self.assertEqual(result["message"], "Upload appears successful.")
        self.assertIsNone(result["course"])
        self.assertEqual(result["assignment"], {"url": UPLOAD_URL, "title": None})
        self.assertEqual(result["upload_url"], UPLOAD_URL)
        self.assertEqual(result["response_url"], UPLOAD_URL)
        self.assertEqual(result["slot_before"]["already_uploaded"], False)
        self.assertEqual(list(result["slot_after"]), ["index", "description", "uploaded", "file_name", "status_text"])
        self.assertEqual(result["slot_after"]["index"], 5)
        self.assertIs(result["slot_after"]["uploaded"], True)
        self.assertEqual(result["file"]["sha256"], DIGEST)
        self.assertEqual((result["requested_file_count"], result["uploaded_file_count"]), (5, 5))

    def test_post_carries_the_form_fields_and_the_bytes_read(self) -> None:
        action = UPLOAD_PATH + "?step=2"
        app, _ = self.app(self.html.replace(FORM_ACTION, f'action="{action}"'))
        post = self.posted_page(app, self.html, url=UPLOAD_URL + "?step=2")
        result = self.direct(app)
        post.assert_called_once()
        self.assertEqual(post.call_args.args, (UPLOAD_URL + "?step=2",))
        self.assertEqual(list(post.call_args.kwargs), ["data", "files", "referer"])
        self.assertEqual(post.call_args.kwargs["referer"], UPLOAD_URL)
        self.assertEqual(post.call_args.kwargs["files"], [(EMPTY_SLOT_FIELD, ("abroad.pdf", CONTENT, "application/pdf"))])
        data = post.call_args.kwargs["data"]
        hidden = self.form["hidden_fields"]
        self.assertGreater(len(hidden), 1)
        self.assertEqual(data, {**hidden, "__EVENTTARGET": SUBMIT_TARGET, "__EVENTARGUMENT": hidden.get("__EVENTARGUMENT", "")})
        self.decode.assert_any_call(post.return_value)
        self.login_check.assert_any_call(post.return_value, html=self.html)
        self.assertEqual(list(result), RESULT_KEYS)
        self.assertEqual(result["response_url"], UPLOAD_URL + "?step=2")

    def test_missing_event_argument_and_unknown_file_type_get_defaults(self) -> None:
        html = (
            f'<html><body><form {FORM_ACTION}><input type="hidden" name="__VIEWSTATE" value="v">'
            f"<a href=\"javascript:__doPostBack('{SUBMIT_TARGET}','')\">Ödevi Gönder</a></form></body></html>"
        )
        app, _ = self.app(html)
        odd = self.files / "notes.zzunknown"
        odd.write_bytes(CONTENT)
        post = self.posted_page(app, "<html><body><form><p>Tamam</p></form></body></html>")
        with patch("ninova_mcp.server.match_upload_slot", return_value=dict(ANY_FILE_SLOT)):
            result = self.direct(app, odd, slot_index=None)
        self.assertEqual(post.call_args.kwargs["data"], {
            "__VIEWSTATE": "v", "__EVENTTARGET": SUBMIT_TARGET, "__EVENTARGUMENT": "",
        })
        self.assertEqual(post.call_args.kwargs["files"], [("f1", ("notes.zzunknown", CONTENT, "application/octet-stream"))])
        # The page after the POST shows no slot: the result says so and keeps the slot that was chosen.
        self.assertIs(result["ok"], False)
        self.assertEqual(result["message"], NOT_SUCCESSFUL)
        self.assertEqual(result["slot_after"], {
            "index": 1, "description": "Any", "uploaded": None, "file_name": None, "status_text": None,
        })
        self.assertEqual(result["slot_before"], {
            "index": 1, "description": "Any", "extensions": None, "field_name": "f1", "already_uploaded": False,
        })
        self.assertEqual((result["requested_file_count"], result["uploaded_file_count"]), (0, 0))

    def test_success_words_on_the_page_count_when_the_slot_is_not_found(self) -> None:
        for body, expected in (
            ("Dosyanızı başarıyla aldık", True), ("Ödevi gönderdiniz", True), ("Dosyayı yüklediniz", True),
            ("Upload success", True), ("Bir hata oluştu", False),
        ):
            with self.subTest(body=body):
                app, _ = self.app()
                self.posted_page(app, f"<html><body><form><p>{body}</p></form></body></html>")
                with patch("ninova_mcp.server.match_upload_slot", return_value=dict(ANY_FILE_SLOT)):
                    result = self.direct(app)
                self.assertIs(result["ok"], expected)
                self.assertEqual(result["message"], "Upload appears successful." if expected else NOT_SUCCESSFUL)
                self.assertIsNone(result["slot_after"]["uploaded"])

    def test_answer_without_a_form_reports_no_counts(self) -> None:
        app, _ = self.app()
        self.posted_page(app, "<html><body><p>Bir hata oluştu</p></body></html>")
        result = self.direct(app)
        self.assertIs(result["ok"], False)
        self.assertEqual(result["slot_after"]["index"], 5)
        self.assertEqual((result["requested_file_count"], result["uploaded_file_count"]), (None, None))

    def test_page_that_still_shows_the_slot_empty_after_the_post(self) -> None:
        # Pinned as it is: the chosen slot is still empty, yet the other slots'
        # "yüklediniz" text on the page makes the result report success.
        app, _ = self.app()
        result = self.submit(app)
        self.assertIs(result["slot_after"]["uploaded"], False)
        self.assertEqual(result["slot_after"]["index"], 5)
        self.assertIs(result["ok"], True)
        self.assertEqual(result["message"], "Upload appears successful.")

    def test_login_page_after_the_post_is_an_auth_error(self) -> None:
        app, _ = self.app()
        post = self.posted_page(app, "<html></html>")
        self.posted_is_login_page = True
        with patch(
            "ninova_mcp.app_ninova.extract_assignment_upload_form", wraps=extract_assignment_upload_form,
        ) as parse:
            with self.assertRaises(NinovaAuthError) as caught:
                self.direct(app)
        self.assertEqual(str(caught.exception), "Session expired during assignment upload.")
        post.assert_called_once()
        self.assertEqual(parse.call_count, 1)

    def test_course_and_assignment_of_the_resolved_target_are_reported(self) -> None:
        app, _ = self.app()
        course = {"code": "STJ", "title": "Staj", "url": BASE + "/Sinif/32945.119022"}
        assignment = {"title": "Internship", "url": BASE + "/Sinif/32945.119022/Odev/245598", "upload_url": UPLOAD_URL}
        with patch.object(app, "_resolve_course", return_value=course), \
                patch.object(app, "get_course_assignments", return_value={"assignments": [assignment]}):
            preview = self.submit(app, upload_url=None, course="STJ", assignment="Internship", confirm=False)
            result = self.submit(app, upload_url=None, course="STJ", assignment="Internship")
        for payload in (preview, result):
            self.assertEqual(payload["course"], course)
            self.assertEqual(payload["assignment"], assignment)
        self.assertIn("STJ", self.prompt)


if __name__ == "__main__":
    unittest.main()
