"""Tests for the public MCP resources and resource templates.

No network: fake ``requests`` sessions serve small synthetic documents, so the
real clients (allowlists, safe redirects, caches, optional-404 handling) run
underneath every read. Names and numbers in the fixtures are made up.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

from ninova_mcp.archive_client import ItuArchiveClient
from ninova_mcp.obs_client import ObsPublicClient
from ninova_mcp.public_client import ItuPublicClient
from ninova_mcp.public_resources import (
    PUBLIC_RESOURCE_TEMPLATES,
    PUBLIC_RESOURCE_URIS,
    RESOURCE_TEMPLATE_URIS,
    PublicResourceError,
    course_code_parameter,
    program_code_parameter,
    read_public_resource,
)
from ninova_mcp.resources import RESOURCE_URIS
from ninova_mcp.server import NinovaMcpApp, build_stdio_server

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE_BASE = "https://itu-ders.com/data"
OBS = "https://obs.itu.edu.tr"
CALENDAR_URL = "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php"

ARCHIVE_DOCUMENTS: dict[str, Any] = {
    "/index.json": {
        "currentTerm": "2026-2027 Güz Dönemi",
        "currentSlug": "2026-2027-guz",
        "scrapedAt": "2026-10-05T09:05:14Z",
        "terms": [
            {"slug": "2026-2027-guz", "label": "2026-2027 Güz Dönemi", "source": "itu-archive"},
            {"slug": "2024-2025-guz", "label": "2024-2025 Güz Dönemi", "missing": True},
        ],
    },
    "/catalog/TST.json": {
        "TST 101E": {
            "code": "TST 101E",
            "name": "Örnek Ders",
            "nameEn": "Sample Course",
            "language": "English",
            "credits": {"theory": 3, "practice": 0, "lab": 0, "local": 3, "ects": 5},
            "description": "Uydurma bir ders tanımı.",
            "outcomes": ["Birinci çıktı"],
            "weeklyTopics": ["Hafta 1 · Giriş"],
            "textbooks": ["Ders Notları"],
            "sourceUrl": "https://obs.itu.edu.tr/public/DersKatalog/x?dersNo=101",
        },
    },
    "/grades/TST.json": [
        {
            "code": "TST 101E", "term": "2023-2024 Güz Dönemi", "donem": "202410", "total": 20,
            "grades": {"AA": 4, "BA": 11, "FF": 5},
            "sourceUrl": "https://obs.itu.edu.tr/public/DersNotDagilimi/x?dersNo=101&yil=2024",
        },
    ],
}

FACULTY_PAGE = (
    '<html><body><select id="akademikBirimId"><option value="">Seçiniz</option>'
    '<option value="7">Örnek Fakültesi</option><option value="9">Deneme Fakültesi</option>'
    "</select></body></html>"
)
PLAN_LIST_PAGE = (
    '<html><body><table class="datalist"><thead><tr><th>İşlem</th><th>Başlık</th></tr></thead>'
    '<tbody><tr><td><a href="/public/DersPlan/DersPlanDetay/2340">Detay</a></td>'
    "<td>Deneme Mühendisliği 2024-2025 Güz Sonrası</td></tr></tbody></table></body></html>"
)
PROGRAMS = {
    "7": [{"programKodu": "ABC_LS", "programAdi": "Örnek Programı"}],
    "9": [{"programKodu": "TSTE_LS", "programAdi": "Deneme Mühendisliği (İngilizce)"}],
}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _response(url: str, status: int, body: str | bytes) -> requests.Response:
    response = requests.Response()
    response.url = url
    response.status_code = status
    response._content = body.encode("utf-8") if isinstance(body, str) else body
    response.encoding = "utf-8"
    return response


class FakeSession:
    """Answers by exact URL (query string excluded); everything else is a 404."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.requested: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requested.append((method, url))
        route = self.routes.get(url)
        if route is None:
            return _response(url, 404, "<html>not found</html>")
        body = route(kwargs) if callable(route) else route
        return _response(url, 200, body if isinstance(body, (str, bytes)) else json.dumps(body))


def make_app() -> tuple[NinovaMcpApp, dict[str, FakeSession]]:
    archive_session = FakeSession(
        {ARCHIVE_BASE + path: document for path, document in ARCHIVE_DOCUMENTS.items()}
    )
    obs_session = FakeSession({
        CALENDAR_URL: fixture("academic_calendar_month_public.html"),
        OBS + "/public/DersPlan": FACULTY_PAGE,
        OBS + "/public/DersPlan/GetAkademikProgramByBirimIdAndPlanTipi": (
            lambda kwargs: PROGRAMS.get(kwargs["data"]["birimId"], [])
        ),
        OBS + "/public/DersPlan/DersPlanlariList": PLAN_LIST_PAGE,
        OBS + "/public/DersPlan/DersPlanDetay/2340": fixture("degree_plan.html"),
    })
    public_session = FakeSession({ItuPublicClient.BUILDINGS_URL: fixture("campus_services.html")})

    app = NinovaMcpApp()
    app._archive = ItuArchiveClient(session=archive_session, base_url=ARCHIVE_BASE, cache_ttl=60)  # type: ignore[arg-type]
    app._obs_public = ObsPublicClient(session=obs_session, cache_ttl=60)  # type: ignore[arg-type]
    app._itu_public = ItuPublicClient(session=public_session, cache_ttl=60)  # type: ignore[arg-type]
    for client in (app._archive, app._obs_public, app._itu_public):
        client._min_request_interval = 0
    return app, {"archive": archive_session, "obs": obs_session, "public": public_session}


def read(app: NinovaMcpApp, uri: str) -> tuple[dict[str, Any], str]:
    contents = list(asyncio.run(build_stdio_server(app).read_resource(uri)))
    assert len(contents) == 1
    return json.loads(contents[0].content), contents[0].mime_type


class ListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.sessions = make_app()
        self.server = build_stdio_server(self.app)

    def test_concrete_resources_are_listed_next_to_the_static_ones(self) -> None:
        listed = {str(r.uri): r for r in asyncio.run(self.server.list_resources())}
        self.assertEqual(sorted(listed), sorted(RESOURCE_URIS + PUBLIC_RESOURCE_URIS))
        for uri in PUBLIC_RESOURCE_URIS:
            self.assertEqual(listed[uri].mimeType, "application/json")
            self.assertTrue(listed[uri].title)
            self.assertTrue(listed[uri].description)

    def test_templates_are_listed(self) -> None:
        templates = {t.uriTemplate: t for t in asyncio.run(self.server.list_resource_templates())}
        self.assertEqual(sorted(templates), sorted(RESOURCE_TEMPLATE_URIS))
        self.assertEqual(
            sorted(templates),
            [
                "itu://archive/catalog/{course_code}",
                "itu://archive/grades/{course_code}",
                "itu://degree-plan/{program_code}",
            ],
        )
        for template in templates.values():
            self.assertEqual(template.mimeType, "application/json")
            self.assertTrue(template.description)

    def test_listing_makes_no_request(self) -> None:
        asyncio.run(self.server.list_resources())
        asyncio.run(self.server.list_resource_templates())
        for session in self.sessions.values():
            self.assertEqual(session.requested, [])

    def test_every_template_parameter_has_a_reader(self) -> None:
        for meta in PUBLIC_RESOURCE_TEMPLATES:
            self.assertIn("{" + meta["parameter"] + "}", meta["uri_template"])

    def test_no_resource_needs_a_login(self) -> None:
        """Reading every resource must work on an app that has no session at all."""
        for uri in (
            *PUBLIC_RESOURCE_URIS,
            "itu://archive/catalog/TST101E",
            "itu://archive/grades/TST101E",
            "itu://degree-plan/TSTE_LS",
        ):
            with self.subTest(uri=uri):
                read(self.app, uri)
        self.assertIsNone(self.app._client)
        self.assertIsNone(self.app._obs)
        self.assertIsNone(self.app._mail)

    def test_static_resources_still_read(self) -> None:
        scale, mime = read(self.app, "itu://reference/grade-scale")
        self.assertEqual(mime, "application/json")
        self.assertEqual(scale["coefficients"]["AA"], 4.0)
        types, _ = read(self.app, "itu://reference/program-types")
        self.assertEqual(types["default"], "LS")


class ConcreteResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.sessions = make_app()

    def assert_provenance(self, data: dict[str, Any], uri: str) -> None:
        self.assertEqual(data["resource_uri"], uri)
        self.assertIs(data["untrusted_external_content"], True)
        self.assertTrue(data["content_notice"])
        self.assertTrue(str(data["source_url"]).startswith("https://"))

    def test_academic_calendar(self) -> None:
        data, mime = read(self.app, "itu://calendar/academic")
        self.assertEqual(mime, "application/json")
        self.assert_provenance(data, "itu://calendar/academic")
        self.assertEqual(data["source_url"], CALENDAR_URL)
        self.assertGreater(data["event_count"], 0)
        self.assertEqual(data["coverage"]["scope"], "displayed_month")
        self.assertIn("Beginning of Fall Term", {e["description"] for e in data["events"]})

    def test_archive_terms(self) -> None:
        data, _ = read(self.app, "itu://archive/terms")
        self.assert_provenance(data, "itu://archive/terms")
        self.assertEqual(data["source_url"], ARCHIVE_BASE + "/index.json")
        self.assertEqual(data["current_slug"], "2026-2027-guz")
        self.assertEqual(data["missing_terms"], ["2024-2025-guz"])

    def test_campus_buildings(self) -> None:
        data, _ = read(self.app, "itu://campus/buildings")
        self.assert_provenance(data, "itu://campus/buildings")
        self.assertEqual(data["source_url"], ItuPublicClient.BUILDINGS_URL)
        self.assertIn({"code": "BBB", "name": "Bilgisayar ve Bilişim Binası"}, data["locations"])
        self.assertNotIn("query", data)

    def test_second_read_is_served_from_the_client_cache(self) -> None:
        read(self.app, "itu://archive/terms")
        read(self.app, "itu://archive/terms")
        self.assertEqual(len(self.sessions["archive"].requested), 1)

    def test_turkish_text_is_not_escaped(self) -> None:
        contents = list(asyncio.run(build_stdio_server(self.app).read_resource("itu://campus/buildings")))
        self.assertIn("Bilişim", contents[0].content)


class TemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.sessions = make_app()

    def test_course_catalog(self) -> None:
        data, mime = read(self.app, "itu://archive/catalog/TST101E")
        self.assertEqual(mime, "application/json")
        self.assertEqual(data["resource_uri"], "itu://archive/catalog/TST101E")
        self.assertEqual(data["course_code"], "TST 101E")
        self.assertEqual(data["coverage"], "covered")
        self.assertEqual(data["course_name_en"], "Sample Course")
        self.assertEqual(data["source_url"], "https://obs.itu.edu.tr/public/DersKatalog/x?dersNo=101")
        self.assertEqual(data["archive_url"], ARCHIVE_BASE + "/catalog/TST.json")
        self.assertIs(data["untrusted_external_content"], True)

    def test_course_code_accepts_percent_encoded_space_and_lower_case(self) -> None:
        data, _ = read(self.app, "itu://archive/catalog/tst%20101e")
        self.assertEqual(data["course_code"], "TST 101E")
        self.assertEqual(data["coverage"], "covered")

    def test_course_grades(self) -> None:
        data, _ = read(self.app, "itu://archive/grades/TST101E")
        self.assertEqual(data["resource_uri"], "itu://archive/grades/TST101E")
        self.assertEqual(data["course_code"], "TST 101E")
        self.assertEqual(data["coverage"], "covered")
        self.assertEqual(data["term_count"], 1)
        self.assertEqual(data["source_url"], ARCHIVE_BASE + "/grades/TST.json")
        self.assertIs(data["untrusted_external_content"], True)

    def test_unknown_course_is_a_clear_answer_not_a_failure(self) -> None:
        data, _ = read(self.app, "itu://archive/catalog/TST999")
        self.assertEqual(data["coverage"], "course_absent_from_catalog")
        self.assertIn("TST 999", data["coverage_note"])
        data, _ = read(self.app, "itu://archive/grades/ZZZ101")
        self.assertEqual(data["coverage"], "branch_grades_missing")

    def test_degree_plan(self) -> None:
        data, mime = read(self.app, "itu://degree-plan/tste_ls")
        self.assertEqual(mime, "application/json")
        self.assertEqual(data["resource_uri"], "itu://degree-plan/TSTE_LS")
        self.assertEqual(data["program_code"], "TSTE_LS")
        self.assertEqual(data["faculty_id"], 9)
        self.assertEqual(data["faculty_name"], "Deneme Fakültesi")
        self.assertEqual(data["selected_plan_id"], 2340)
        self.assertEqual(data["plan"]["course_count"], 1)
        self.assertEqual(data["source_url"], OBS + "/public/DersPlan/DersPlanDetay/2340")
        self.assertIs(data["untrusted_external_content"], True)

    def test_unknown_program_code_is_a_clear_error(self) -> None:
        with self.assertRaises(ValueError) as raised:
            read(self.app, "itu://degree-plan/ZZZ_LS")
        message = str(raised.exception)
        self.assertIn("ZZZ_LS", message)
        self.assertIn("bulunamadı", message)
        self.assertNotIn("Traceback", message)
        # Only the official faculty and program lists were read; the unknown
        # code was never put into a request.
        for _, url in self.sessions["obs"].requested:
            self.assertNotIn("ZZZ", url)
            self.assertNotIn("DersPlanlariList", url)


class ParameterValidationTests(unittest.TestCase):
    BAD_URIS = [
        "itu://archive/catalog/..%2F..%2Fsecrets",
        "itu://archive/catalog/BLG",
        "itu://archive/catalog/BLG223E%3Fx%3D1",
        "itu://archive/catalog/" + "A" * 80,
        "itu://archive/grades/%2E%2E",
        "itu://archive/grades/223E",
        "itu://degree-plan/BLG",
        "itu://degree-plan/BLG_LS%2F..%2Fadmin",
        "itu://degree-plan/BLG_LS%26x%3D1",
        "itu://degree-plan/%20",
    ]

    def test_bad_parameters_are_rejected_before_any_request(self) -> None:
        app, sessions = make_app()
        for uri in self.BAD_URIS:
            with self.subTest(uri=uri):
                with self.assertRaises(ValueError) as raised:
                    read(app, uri)
                self.assertNotIn("Traceback", str(raised.exception))
        for session in sessions.values():
            self.assertEqual(session.requested, [])

    def test_validators(self) -> None:
        self.assertEqual(course_code_parameter("blg%20223e"), ("BLG", "BLG 223E"))
        self.assertEqual(course_code_parameter("CEN4901E"), ("CEN", "CEN 4901E"))
        self.assertEqual(program_code_parameter("ses_th_ls"), "SES_TH_LS")
        for bad in ("", "BLG/223E", "BLG 223E?x", "../x"):
            with self.subTest(bad=bad), self.assertRaises(PublicResourceError):
                course_code_parameter(bad)
        for bad in ("", "BLG", "BLG LS", "BLG_LS/x", "_LS", "BLG_LS_X_Y_Z"):
            with self.subTest(bad=bad), self.assertRaises(PublicResourceError):
                program_code_parameter(bad)

    def test_a_slash_in_the_uri_does_not_match_a_template(self) -> None:
        app, sessions = make_app()
        with self.assertRaises(ValueError):
            read(app, "itu://archive/catalog/BLG/223E")
        self.assertEqual(sessions["archive"].requested, [])


class ErrorMessageTests(unittest.TestCase):
    def test_client_errors_keep_their_message(self) -> None:
        app, _ = make_app()
        app._archive = ItuArchiveClient(session=FakeSession({}), base_url=ARCHIVE_BASE, cache_ttl=60)  # type: ignore[arg-type]
        app._archive._min_request_interval = 0
        # The SDK logs the failure server-side and sends the client the message only.
        with self.assertLogs("mcp.server.fastmcp.server", level="ERROR"):
            with self.assertRaises(Exception) as raised:
                read(app, "itu://archive/terms")
        self.assertEqual(
            str(raised.exception),
            "Error reading resource itu://archive/terms: Archive returned HTTP 404 for /index.json",
        )

    def test_unexpected_errors_do_not_leak_internals(self) -> None:
        def broken(app: Any) -> dict[str, Any]:
            del app
            raise RuntimeError("C:\\secret\\path token=abc")

        with self.assertRaises(PublicResourceError) as raised:
            read_public_resource(broken, object())
        self.assertEqual(str(raised.exception), "Kaynak okunamadı (RuntimeError).")
        self.assertIsNone(raised.exception.__cause__)


class RemoteTransportTests(unittest.TestCase):
    def test_remote_server_registers_the_same_resources(self) -> None:
        with patch.dict(os.environ, {"NINOVA_REMOTE_API_KEY": "", "NINOVA_REMOTE_REQUIRE_API_KEY": "0"}):
            from ninova_mcp import remote

            app, _ = make_app()
            mcp = remote._build_fastmcp(app, mount_path="/mcp")
        uris = sorted(str(r.uri) for r in asyncio.run(mcp.list_resources()))
        self.assertEqual(uris, sorted(RESOURCE_URIS + PUBLIC_RESOURCE_URIS))
        templates = sorted(t.uriTemplate for t in asyncio.run(mcp.list_resource_templates()))
        self.assertEqual(templates, sorted(RESOURCE_TEMPLATE_URIS))


class ProtocolTests(unittest.TestCase):
    """Templates and their errors over the real stdio protocol."""

    def test_templates_listed_and_bad_parameter_error_is_clean(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.shared.exceptions import McpError

        async def run() -> tuple[list[str], str]:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            env.setdefault("NINOVA_USERNAME", "dummy")
            env.setdefault("NINOVA_PASSWORD", "dummy")
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "ninova_mcp"], env=env, cwd=str(ROOT),
            )
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    listed = await session.list_resource_templates()
                    templates = [t.uriTemplate for t in listed.resourceTemplates]
                    try:
                        # Rejected by validation, so this never reaches the network.
                        await session.read_resource("itu://archive/catalog/not-a-code")  # type: ignore[arg-type]
                    except McpError as exc:
                        return templates, str(exc)
                    return templates, ""

        templates, error = asyncio.run(run())
        self.assertEqual(sorted(templates), sorted(RESOURCE_TEMPLATE_URIS))
        self.assertIn("Ders kodu çözümlenemedi", error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn("File \"", error)


if __name__ == "__main__":
    unittest.main()
