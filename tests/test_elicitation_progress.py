"""Server-side confirmation (elicitation) and progress, through the real SDK.

Every test here drives a FastMCP server with the SDK's in-memory client
session. Nothing reaches the network: OBS and Ninova are fakes, and the one
function that would POST is replaced by a recorder.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

import test_registration_tools as registration_fixtures
import test_tracking_completeness as tracking_fixtures
from ninova_mcp import interaction, registration_write
from ninova_mcp.obs_client import ObsPublicClient
from ninova_mcp.registration_tools import get_elective_group
from ninova_mcp.server import NinovaMcpApp, TOOLS, register_tools

UPLOAD_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "odev_gonder.html"
BASE = "https://ninova.itu.edu.tr"
UPLOAD_URL = f"{BASE}/Sinif/32945.119022/Odev/245598/OdevGonder"
DRAFT = "obs_save_registration_draft"
UPLOAD = "submit_assignment"


def accept(_params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="accept", content={"onay": True})


def decline(_params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="decline")


def cancel(_params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="cancel")


class Outcome:
    def __init__(self, result: types.CallToolResult, prompts: list[str], progress: list[tuple]) -> None:
        self.is_error = result.isError
        self.text = result.content[0].text if result.content else ""
        self.data = None if result.isError else json.loads(self.text)
        self.prompts = prompts
        self.progress = progress


def call(
    server: FastMCP, name: str, arguments: dict[str, Any], *,
    answer: Any = None, want_progress: bool = False, on_progress: Any = None,
) -> Outcome:
    """One tools/call over a real client session.

    ``answer`` decides the client's elicitation reply; without it the client
    does not declare the elicitation capability at all.
    """
    prompts: list[str] = []
    progress: list[tuple] = []

    async def on_elicit(_context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        prompts.append(params.message)
        return answer(params)

    async def progress_callback(done: float, total: float | None, message: str | None) -> None:
        progress.append((done, total, message))
        if on_progress is not None:
            on_progress()

    async def run() -> types.CallToolResult:
        async with create_connected_server_and_client_session(
            server, elicitation_callback=on_elicit if answer is not None else None,
        ) as session:
            return await session.call_tool(
                name, arguments, progress_callback=progress_callback if want_progress else None,
            )

    return Outcome(asyncio.run(run()), prompts, progress)


def make_server(app: Any, names: list[str]) -> FastMCP:
    server = FastMCP("interaction-test")
    register_tools(server, app, names)
    return server


class FakeDraftObs:
    """Runs the real draft writer; only the POST itself is a recorder."""

    base_url = "https://obs.itu.edu.tr"

    def __init__(self, current: list[str]) -> None:
        self.current = list(current)
        self.posts: list[list[str]] = []
        self.readable = True

    def get_registration_draft(self) -> dict[str, Any]:
        if not self.readable:
            return {"term_code": "202710", "draft_creation_allowed": True, "courses": "unreadable"}
        return {
            "term_code": "202710", "draft_creation_allowed": True, "draft_exists": bool(self.current),
            "courses": [{"crn": crn} for crn in self.current],
        }

    def save_registration_draft(self, crns: list[str], **kwargs: Any) -> dict[str, Any]:
        return registration_write.save_registration_draft(self, crns, **kwargs)

    def post(self, _obs: Any, _path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.posts.append(list(payload["ecrn"]))
        self.current = list(payload["ecrn"])
        return {"status": "acknowledged", "http_status": 200, "status_code": 0, "result_code": None}


class InteractionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        stack = self.enterContext(ExitStack())
        self.directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="itu-interaction-")))
        environment = {
            "NINOVA_STATE_DIR": str(self.directory),
            "NINOVA_OBS_REGISTRATION_WRITES": "1",
            "NINOVA_USERNAME": "dummy",
            "NINOVA_PASSWORD": "dummy",
        }
        stack.enter_context(patch.dict(os.environ, environment))
        os.environ.pop(interaction.REQUIRE_ELICITATION_ENV, None)
        os.environ.pop("NINOVA_ALLOW_UPLOADS", None)
        stack.enter_context(patch("ninova_mcp.server.load_ninova_env"))
        stack.enter_context(patch(
            "requests.sessions.Session.request", side_effect=AssertionError("Network forbidden in interaction test"),
        ))
        self.stack = stack


class DraftElicitationTests(InteractionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = NinovaMcpApp()
        self.obs = FakeDraftObs(["10001", "10009"])
        self.app._obs = self.obs
        self.stack.enter_context(patch("ninova_mcp.registration_write._post_once", self.obs.post))
        self.server = make_server(self.app, [DRAFT])
        self.arguments = {"crns": ["10001", "10002"], "confirm": True, "allow_replace": True}

    def test_accept_writes_once_and_states_the_exact_change(self) -> None:
        outcome = call(self.server, DRAFT, self.arguments, answer=accept)
        self.assertFalse(outcome.is_error)
        self.assertEqual(self.obs.posts, [["10001", "10002"]])
        self.assertEqual(outcome.data["status"], "saved")
        self.assertEqual(outcome.data["confirmation"]["method"], "elicitation")
        self.assertEqual(outcome.data["confirmation"]["user_action"], "accept")
        self.assertEqual(len(outcome.prompts), 1)
        message = outcome.prompts[0]
        self.assertIn("Dönem kodu: 202710", message)
        self.assertIn("Kaydedilecek CRN'ler (2): 10001, 10002", message)
        self.assertIn("Taslağa eklenecek CRN'ler (1): 10002", message)
        self.assertIn("Taslaktan ÇIKARILACAK CRN'ler (1): 10009", message)
        self.assertIn("derse kayıt yapmaz", message)

    def test_message_without_removals_and_with_unreadable_draft(self) -> None:
        self.obs.current = ["10001"]
        outcome = call(self.server, DRAFT, self.arguments, answer=accept)
        self.assertIn("Taslaktan çıkarılacak CRN yok.", outcome.prompts[0])
        self.assertNotIn("ÇIKARILACAK", outcome.prompts[0])

        self.obs.readable = False
        outcome = call(self.server, DRAFT, {**self.arguments, "crns": ["10005"]}, answer=decline)
        self.assertIn("Mevcut taslak okunamadı", outcome.prompts[0])
        self.assertEqual(self.obs.posts, [["10001", "10002"]])

    def test_decline_and_cancel_send_nothing_and_are_not_errors(self) -> None:
        for answer, status, action in (
            (decline, "declined_by_user", "decline"), (cancel, "cancelled_by_user", "cancel"),
        ):
            with self.subTest(status=status):
                outcome = call(self.server, DRAFT, self.arguments, answer=answer)
                self.assertFalse(outcome.is_error)
                self.assertEqual(outcome.data["status"], status)
                self.assertIs(outcome.data["saved"], False)
                self.assertIsNone(outcome.data["submission"])
                self.assertEqual(outcome.data["confirmation"]["user_action"], action)
                self.assertIn("do not call the tool again", outcome.data["next_step"])
                self.assertEqual(len(outcome.prompts), 1)
        self.assertEqual(self.obs.posts, [])
        self.assertEqual(self.obs.current, ["10001", "10009"])

    def test_accept_without_the_ticked_answer_is_not_a_yes(self) -> None:
        answers = {
            "declined_by_user": lambda _p: types.ElicitResult(action="accept", content={"onay": False}),
            "confirmation_unavailable": lambda _p: types.ElicitResult(action="accept", content={}),
        }
        for status, answer in answers.items():
            with self.subTest(status=status):
                outcome = call(self.server, DRAFT, self.arguments, answer=answer)
                self.assertFalse(outcome.is_error)
                self.assertEqual(outcome.data["status"], status)
        self.assertEqual(self.obs.posts, [])

    def test_failing_client_dialog_sends_nothing(self) -> None:
        def broken(_params: Any) -> types.ErrorData:
            return types.ErrorData(code=types.INTERNAL_ERROR, message="dialog failed")

        outcome = call(self.server, DRAFT, self.arguments, answer=broken)
        self.assertEqual(outcome.data["status"], "confirmation_unavailable")
        self.assertEqual(self.obs.posts, [])

    def test_model_supplied_confirm_never_skips_the_dialog(self) -> None:
        # confirm=true and allow_replace=true are both set by the caller; the
        # user's decline still wins, on every attempt.
        for _attempt in range(3):
            outcome = call(self.server, DRAFT, self.arguments, answer=decline)
            self.assertEqual(outcome.data["status"], "declined_by_user")
        self.assertEqual(self.obs.posts, [])

    def test_client_without_elicitation_follows_argument_path_and_says_so(self) -> None:
        outcome = call(self.server, DRAFT, self.arguments)
        self.assertEqual(self.obs.posts, [["10001", "10002"]])
        self.assertEqual(outcome.data["status"], "saved")
        self.assertEqual(outcome.data["confirmation"]["method"], "argument_only")
        self.assertIs(outcome.data["confirmation"]["elicitation_supported"], False)
        self.assertIn("no MCP elicitation support", outcome.data["confirmation"]["note"])

    def test_require_variable_refuses_clients_without_elicitation(self) -> None:
        os.environ[interaction.REQUIRE_ELICITATION_ENV] = "1"
        outcome = call(self.server, DRAFT, self.arguments)
        self.assertFalse(outcome.is_error)
        self.assertEqual(outcome.data["status"], "elicitation_required")
        self.assertIs(outcome.data["saved"], False)
        self.assertEqual(self.obs.posts, [])
        # A capable client is unaffected by the variable.
        outcome = call(self.server, DRAFT, self.arguments, answer=accept)
        self.assertEqual(outcome.data["status"], "saved")
        self.assertEqual(self.obs.posts, [["10001", "10002"]])

    def test_require_variable_accepts_the_usual_spellings_and_is_default_off(self) -> None:
        for value in ("", "  ", "0", "false", "No", "OFF"):
            with self.subTest(value=value):
                os.environ[interaction.REQUIRE_ELICITATION_ENV] = value
                self.assertFalse(interaction.elicitation_required())
        for value in ("1", "true", "TRUE", "yes", "On", " 1 "):
            with self.subTest(value=value):
                os.environ[interaction.REQUIRE_ELICITATION_ENV] = value
                self.assertTrue(interaction.elicitation_required())
        os.environ.pop(interaction.REQUIRE_ELICITATION_ENV)
        self.assertFalse(interaction.elicitation_required())

    def test_require_variable_spelled_true_refuses_a_client_without_elicitation(self) -> None:
        os.environ[interaction.REQUIRE_ELICITATION_ENV] = "true"
        outcome = call(self.server, DRAFT, self.arguments)
        self.assertEqual(outcome.data["status"], "elicitation_required")
        self.assertEqual(self.obs.posts, [])

    def test_unrecognised_require_value_fails_clearly_and_sends_nothing(self) -> None:
        from ninova_mcp.operator_settings import OperatorSettingError

        for value in ("enabled", "2", "evet"):
            with self.subTest(value=value):
                os.environ[interaction.REQUIRE_ELICITATION_ENV] = value
                with self.assertRaisesRegex(OperatorSettingError, interaction.REQUIRE_ELICITATION_ENV):
                    interaction.elicitation_required()
                # Even a client that can show the dialog does not get a write through.
                for answer in (accept, None):
                    outcome = call(self.server, DRAFT, self.arguments, answer=answer)
                    self.assertTrue(outcome.is_error)
                    self.assertIn(interaction.REQUIRE_ELICITATION_ENV, outcome.text)
        self.assertEqual(self.obs.posts, [])

    def test_earlier_guards_run_before_any_dialog(self) -> None:
        # Missing confirmation and invalid CRNs are schema errors.
        for arguments in ({"crns": ["10002"], "confirm": False}, {"crns": ["1"], "confirm": True}):
            outcome = call(self.server, DRAFT, arguments, answer=accept)
            self.assertTrue(outcome.is_error)
            self.assertEqual(outcome.prompts, [])
        # Dropping a course without allow_replace stops before the dialog.
        outcome = call(self.server, DRAFT, {"crns": ["10001", "10002"], "confirm": True}, answer=accept)
        self.assertEqual(outcome.data["status"], "needs_allow_replace")
        self.assertEqual(outcome.prompts, [])
        # An unchanged draft sends nothing, so there is nothing to confirm.
        outcome = call(self.server, DRAFT, {"crns": ["10009", "10001"], "confirm": True}, answer=accept)
        self.assertEqual(outcome.data["status"], "unchanged")
        self.assertEqual(outcome.prompts, [])
        # The operator switch is checked first.
        os.environ.pop("NINOVA_OBS_REGISTRATION_WRITES")
        outcome = call(self.server, DRAFT, self.arguments, answer=accept)
        self.assertTrue(outcome.is_error)
        self.assertIn("disabled", outcome.text)
        self.assertEqual(outcome.prompts, [])
        self.assertEqual(self.obs.posts, [])

    def test_direct_method_call_keeps_the_argument_only_contract(self) -> None:
        result = self.app.obs_save_registration_draft(["10001", "10002"], True, True)
        self.assertEqual(result["status"], "saved")
        self.assertNotIn("confirmation", result)
        self.assertEqual(self.obs.posts, [["10001", "10002"]])

    def test_direct_method_call_is_refused_when_elicitation_is_required(self) -> None:
        os.environ[interaction.REQUIRE_ELICITATION_ENV] = "1"
        result = self.app.obs_save_registration_draft(["10001", "10002"], True, True)
        self.assertEqual(result["status"], "elicitation_required")
        self.assertEqual(self.obs.posts, [])


class UploadElicitationTests(InteractionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.html = UPLOAD_FIXTURE.read_text(encoding="utf-8")
        self.file_path = self.directory / "rapor.pdf"
        self.file_path.write_bytes(b"%PDF-1.4 " + b"x" * 2039)
        self.app = NinovaMcpApp()
        page = MagicMock()
        page.url = UPLOAD_URL
        posted = MagicMock()
        posted.url = UPLOAD_URL
        self.resolved = {
            "upload_url": UPLOAD_URL,
            "course": {"code": "BLG 223E", "title": "Data Structures", "url": f"{BASE}/Sinif/1.2"},
            "assignment": {"title": "Homework 2", "url": f"{BASE}/Sinif/1.2/Odev/1"},
        }
        client = self.app.client
        self.stack.enter_context(patch.object(self.app, "_resolve_assignment_upload_url", return_value=self.resolved))
        self.stack.enter_context(patch.object(client, "get_html", return_value=(self.html, page)))
        self.post = self.stack.enter_context(patch.object(client, "post_multipart", return_value=posted))
        self.stack.enter_context(patch.object(client, "_decode_response", return_value=self.html))
        self.stack.enter_context(patch.object(client, "_looks_like_login_page", return_value=False))
        self.server = make_server(self.app, [UPLOAD])
        self.arguments = {
            "file_path": str(self.file_path), "upload_url": UPLOAD_URL, "slot_index": 5, "confirm": True,
        }

    def test_accept_uploads_once_and_names_what_is_sent(self) -> None:
        outcome = call(self.server, UPLOAD, self.arguments, answer=accept)
        self.assertFalse(outcome.is_error)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["files"][0][1][0], "rapor.pdf")
        self.assertEqual(outcome.data["confirmation"]["method"], "elicitation")
        message = outcome.prompts[0]
        self.assertIn("Ders: BLG 223E - Data Structures", message)
        self.assertIn("Ödev: Homework 2", message)
        self.assertIn("Dosya: rapor.pdf, 2,0 KB (2048 bayt)", message)
        self.assertIn("Yükleme alanı: 5 (", message)
        self.assertIn("mevcut bir dosyanın üzerine yazılmayacak", message)
        self.assertNotIn("YERİNE", message)

    def test_replacing_an_existing_submission_is_stated(self) -> None:
        arguments = {**self.arguments, "slot_index": 1, "allow_replace": True}
        slot = {
            "index": 1, "description": "Report", "field_name": "f1", "uploaded": True,
            "allowed_extensions": [], "extensions": None,
        }
        with patch("ninova_mcp.server.match_upload_slot", return_value=slot):
            outcome = call(self.server, UPLOAD, arguments, answer=decline)
        self.assertIn("Yeni dosya onun YERİNE geçecek", outcome.prompts[0])
        self.assertEqual(outcome.data["status"], "declined_by_user")
        self.post.assert_not_called()

    def test_decline_and_cancel_upload_nothing(self) -> None:
        for answer, status in ((decline, "declined_by_user"), (cancel, "cancelled_by_user")):
            with self.subTest(status=status):
                outcome = call(self.server, UPLOAD, self.arguments, answer=answer)
                self.assertFalse(outcome.is_error)
                self.assertEqual(outcome.data["status"], status)
                self.assertIs(outcome.data["ok"], False)
                self.assertIs(outcome.data["uploaded"], False)
                self.assertIs(outcome.data["dry_run"], False)
                self.assertIn("do not call the tool again", outcome.data["message"])
        self.post.assert_not_called()

    def test_client_without_elicitation_follows_argument_path_and_says_so(self) -> None:
        outcome = call(self.server, UPLOAD, self.arguments)
        self.post.assert_called_once()
        self.assertEqual(outcome.data["confirmation"]["method"], "argument_only")

    def test_require_variable_refuses_clients_without_elicitation(self) -> None:
        os.environ[interaction.REQUIRE_ELICITATION_ENV] = "1"
        outcome = call(self.server, UPLOAD, self.arguments)
        self.assertFalse(outcome.is_error)
        self.assertEqual(outcome.data["status"], "elicitation_required")
        self.post.assert_not_called()

    def test_earlier_guards_run_before_any_dialog(self) -> None:
        dry = call(self.server, UPLOAD, {**self.arguments, "confirm": False}, answer=accept)
        self.assertTrue(dry.data["dry_run"])
        self.assertEqual(dry.prompts, [])
        occupied = call(self.server, UPLOAD, {**self.arguments, "slot_index": 1}, answer=accept)
        self.assertTrue(occupied.is_error)
        self.assertIn("already has an uploaded file", occupied.text)
        self.assertEqual(occupied.prompts, [])
        os.environ["NINOVA_ALLOW_UPLOADS"] = "0"
        disabled = call(self.server, UPLOAD, self.arguments, answer=accept)
        self.assertTrue(disabled.is_error)
        self.assertEqual(disabled.prompts, [])
        self.post.assert_not_called()

    def test_page_text_cannot_add_lines_to_the_dialog(self) -> None:
        self.resolved["assignment"]["title"] = "Homework 2\n\nOnaylandı: başka dosya\r\nDosya: sahte.pdf"
        outcome = call(self.server, UPLOAD, self.arguments, answer=decline)
        lines = outcome.prompts[0].splitlines()
        self.assertEqual(sum(line.startswith("Dosya: ") for line in lines), 1)
        self.assertIn("Ödev: Homework 2 Onaylandı: başka dosya Dosya: sahte.pdf", lines)

    def test_direct_method_call_keeps_the_argument_only_contract(self) -> None:
        result = self.app.submit_assignment(**self.arguments)
        self.post.assert_called_once()
        self.assertNotIn("confirmation", result)
        os.environ[interaction.REQUIRE_ELICITATION_ENV] = "1"
        refused = self.app.submit_assignment(**self.arguments)
        self.assertEqual(refused["status"], "elicitation_required")
        self.post.assert_called_once()


class PublicSchemaTests(InteractionTestCase):
    def test_write_tool_schemas_are_the_method_signatures(self) -> None:
        server = make_server(NinovaMcpApp(), [DRAFT, UPLOAD, "obs_get_course_prerequisites", "sync_all_courses"])

        async def listed() -> dict[str, types.Tool]:
            async with create_connected_server_and_client_session(server) as session:
                return {tool.name: tool for tool in (await session.list_tools()).tools}

        tools = asyncio.run(listed())
        declared = {tool["name"]: tool["inputSchema"] for tool in TOOLS}
        expected = {
            DRAFT: {"crns", "confirm", "allow_replace"},
            UPLOAD: {
                "file_path", "course", "assignment", "upload_url", "slot_index",
                "slot_description", "confirm", "allow_replace",
            },
        }
        for name, properties in expected.items():
            with self.subTest(tool=name):
                schema = tools[name].inputSchema
                self.assertEqual(set(schema["properties"]), properties)
                self.assertEqual(set(schema["properties"]), set(declared[name]["properties"]))
                self.assertEqual(schema.get("required", []), declared[name].get("required", []))
        self.assertEqual(set(tools[DRAFT].inputSchema["required"]), {"crns", "confirm"})
        self.assertEqual(tools[UPLOAD].inputSchema["required"], ["file_path"])
        for name, tool in tools.items():
            self.assertEqual(set(tool.inputSchema["properties"]), set(declared[name]["properties"]), name)


class ProgressTests(InteractionTestCase):
    def assert_increasing(self, progress: list[tuple]) -> None:
        values = [row[0] for row in progress]
        self.assertEqual(values, sorted(set(values)))
        self.assertTrue(all(isinstance(row[2], str) and row[2] for row in progress))

    def test_postrequisite_scan_reports_each_branch_while_it_runs(self) -> None:
        app = NinovaMcpApp()
        public = ObsPublicClient()
        app._obs_public = public
        first_arrived = threading.Event()
        waited: list[bool] = []

        def read_branch(branch: str) -> dict[str, Any]:
            # The scan cannot continue until the client has received the first
            # notification. A server that only flushed at the end would stall.
            if not waited:
                waited.append(first_arrived.wait(10))
            return {"table_parsed": True, "url": f"https://obs.itu.edu.tr/public/{branch}", "rules": {}}

        index = {"blg": 1, "fiz": 2, "kim": 3, "mat": 4, "9": 9}
        with patch.object(public, "_build_course_index", return_value=index), \
                patch.object(public, "get_branch_prerequisites", side_effect=read_branch), \
                patch.object(public, "resolve_course_code", return_value={"code": "BLG 102E"}):
            outcome = call(
                make_server(app, ["obs_get_course_prerequisites"]), "obs_get_course_prerequisites",
                {"course_code": "BLG 102E", "direction": "postrequisites"},
                want_progress=True, on_progress=first_arrived.set,
            )
        self.assertFalse(outcome.is_error)
        self.assertEqual(waited, [True])
        self.assertEqual([(done, total) for done, total, _ in outcome.progress], [(n, 4) for n in range(5)])
        self.assert_increasing(outcome.progress)
        self.assertEqual(outcome.progress[0][2], "Ön şart tablosu taranıyor (1/4): BLG")
        self.assertEqual(outcome.data["postreq_scanned_branches"], 4)

    def test_sync_all_courses_reports_course_counts(self) -> None:
        app = NinovaMcpApp()
        app.tracking_state_path = self.directory / "tracking.json"
        app._client = tracking_fixtures.SyntheticClient()
        courses = [dict(tracking_fixtures.COURSE), {**tracking_fixtures.COURSE, "url": tracking_fixtures.COURSE["url"] + "x", "code": "SEC 102"}]
        app.list_courses = Mock(return_value={"courses": courses, "enrollment_coverage": {"status": "complete"}})
        outcome = call(make_server(app, ["sync_all_courses"]), "sync_all_courses", {}, want_progress=True)
        self.assertFalse(outcome.is_error)
        self.assertEqual([(done, total) for done, total, _ in outcome.progress], [(0, 2), (1, 2), (2, 2)])
        self.assert_increasing(outcome.progress)
        self.assertTrue(outcome.progress[0][2].startswith("Ders eşitleniyor (1/2): "))
        self.assertIn("SEC 102", outcome.progress[1][2])

    def test_elective_group_reports_reads_then_each_section_check(self) -> None:
        mock_app = registration_fixtures.make_app()
        mock_app.obs_public.get_course_schedule.return_value["courses"] = [
            registration_fixtures.section(), registration_fixtures.section("10002", "BLG 201"),
        ]
        mock_app.obs.validate_registration_crns.side_effect = lambda crns: {
            crns[0]: registration_fixtures.verdict(crns[0], "BLG 201"),
        }

        def obs_get_elective_group(group_id: int) -> dict[str, Any]:
            return get_elective_group(mock_app, group_id)

        holder = SimpleNamespace(obs_get_elective_group=obs_get_elective_group)
        group = registration_fixtures.elective_group(731, ["BLG 201"])
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=group):
            outcome = call(
                make_server(holder, ["obs_get_elective_group"]), "obs_get_elective_group",
                {"group_id": 731}, want_progress=True,
            )
        self.assertFalse(outcome.is_error)
        self.assertEqual(outcome.data["eligibility_checked_section_count"], 2)
        self.assertEqual(
            [(done, total) for done, total, _ in outcome.progress],
            [(0, None), (1, None), (2, 4), (3, 4), (4, 4)],
        )
        self.assert_increasing(outcome.progress)
        self.assertEqual(outcome.progress[2][2], "Şube uygunluğu kontrol ediliyor (1/2): CRN 10001")

    def test_crawl_course_reports_pages_without_a_total(self) -> None:
        app = NinovaMcpApp()
        url = f"{BASE}/Sinif/1.2"
        html = f'<html><body><h1>Ders</h1><a href="{url}/Duyurular">Duyurular</a></body></html>'
        with patch.object(app.client, "get_html", side_effect=lambda page: (html, SimpleNamespace(url=page))):
            outcome = call(
                make_server(app, ["crawl_course"]), "crawl_course",
                {"course_url": url, "max_depth": 1, "max_pages": 5}, want_progress=True,
            )
        self.assertFalse(outcome.is_error)
        self.assertEqual(outcome.data["pages_crawled"], len(outcome.progress))
        self.assertGreaterEqual(len(outcome.progress), 1)
        self.assertTrue(all(total is None for _, total, _ in outcome.progress))
        self.assert_increasing(outcome.progress)

    def test_no_progress_token_and_direct_calls_are_silent(self) -> None:
        app = NinovaMcpApp()
        public = ObsPublicClient()
        app._obs_public = public
        seen: list[Any] = []

        def read_branch(_branch: str) -> dict[str, Any]:
            seen.append(interaction.progress_callback())
            return {"table_parsed": True, "url": "https://obs.itu.edu.tr/public/x", "rules": {}}

        with patch.object(public, "_build_course_index", return_value={"blg": 1, "mat": 2}), \
                patch.object(public, "get_branch_prerequisites", side_effect=read_branch), \
                patch.object(public, "resolve_course_code", return_value={"code": "BLG 102E"}):
            arguments = {"course_code": "BLG 102E", "direction": "postrequisites"}
            # A client that sent no progress token.
            outcome = call(make_server(app, ["obs_get_course_prerequisites"]), "obs_get_course_prerequisites", arguments)
            self.assertFalse(outcome.is_error)
            self.assertEqual(outcome.progress, [])
            # A plain method call with no MCP request at all.
            public._cache.clear()
            direct = app.obs_get_course_prerequisites(**arguments)
            self.assertEqual(direct["postreq_scanned_branches"], 2)
            # The client class takes a plain callback and survives its failure.
            public._cache.clear()
            reported: list[tuple] = []
            public.get_postrequisites("BLG 102E", progress=lambda *row: reported.append(row))
            public._cache.clear()
            public.get_postrequisites("BLG 102E", progress=Mock(side_effect=RuntimeError("listener failed")))
        self.assertEqual(seen, [None] * 8)
        self.assertEqual([row[:2] for row in reported], [(0, 2), (1, 2), (2, 2)])

    def test_steps_are_inert_without_a_listener_and_ignore_listener_errors(self) -> None:
        self.assertIsNone(interaction.current())
        steps = interaction.progress_steps(3)
        steps.advance("bir")
        steps.finish("bitti")
        self.assertEqual(steps.done, 3)
        self.assertEqual(interaction.progress_kwargs(), {})
        failing = interaction.ProgressSteps(2, Mock(side_effect=RuntimeError("listener failed")))
        failing.advance("bir")
        failing.finish("bitti")


class ToolRunnerTests(InteractionTestCase):
    def test_other_tools_wait_while_a_dialog_is_open(self) -> None:
        """Calls stay one at a time even though the write tool leaves the loop free."""
        app = NinovaMcpApp()
        obs = FakeDraftObs(["10001"])
        app._obs = obs
        order: list[str] = []

        def auth_status() -> dict[str, Any]:
            order.append("auth_status")
            return {"ok": True}

        app.auth_status = auth_status  # type: ignore[method-assign]
        server = make_server(app, [DRAFT, "auth_status"])

        async def run() -> None:
            dialog_open = asyncio.Event()
            release = asyncio.Event()

            async def on_elicit(_context: Any, _params: Any) -> types.ElicitResult:
                dialog_open.set()
                await release.wait()
                order.append("answered")
                return types.ElicitResult(action="decline")

            async with create_connected_server_and_client_session(server, elicitation_callback=on_elicit) as session:
                write = asyncio.create_task(session.call_tool(DRAFT, {"crns": ["10002"], "confirm": True, "allow_replace": True}))
                await dialog_open.wait()
                read = asyncio.create_task(session.call_tool("auth_status", {}))
                await asyncio.sleep(0.2)
                self.assertEqual(order, [])
                release.set()
                await write
                await read

        with patch("ninova_mcp.registration_write._post_once", obs.post):
            asyncio.run(run())
        self.assertEqual(order, ["answered", "auth_status"])
        self.assertEqual(obs.posts, [])

    def test_request_cancelled_while_the_dialog_is_open_sends_nothing(self) -> None:
        app = NinovaMcpApp()
        obs = FakeDraftObs(["10001"])
        app._obs = obs
        server = make_server(app, [DRAFT, "auth_status"])
        seen: dict[str, Any] = {}

        async def run() -> None:
            dialog_open = asyncio.Event()
            release = asyncio.Event()

            async def on_elicit(_context: Any, _params: Any) -> types.ElicitResult:
                dialog_open.set()
                await release.wait()
                return types.ElicitResult(action="accept", content={"onay": True})

            async with create_connected_server_and_client_session(server, elicitation_callback=on_elicit) as session:
                arguments = {"crns": ["10002"], "confirm": True, "allow_replace": True}
                write = asyncio.create_task(session.call_tool(DRAFT, arguments))
                await dialog_open.wait()
                # The tools/call above is the first request after initialize.
                await session.send_notification(types.ClientNotification(types.CancelledNotification(
                    params=types.CancelledNotificationParams(requestId=1, reason="test"),
                )))
                await asyncio.sleep(0.2)
                release.set()  # an accept that arrives after the cancellation
                await asyncio.wait({write}, timeout=5)
                seen["write_error"] = write.exception()
                # The server is still usable and the runner's lock was released.
                seen["next"] = await asyncio.wait_for(session.call_tool("auth_status", {}), 5)

        with patch("ninova_mcp.registration_write._post_once", obs.post):
            asyncio.run(run())
        self.assertIsNotNone(seen["write_error"])
        # Answered at all (its content depends on the offline fake login).
        self.assertIsInstance(seen["next"], types.CallToolResult)
        self.assertEqual(obs.posts, [])


if __name__ == "__main__":
    unittest.main()
