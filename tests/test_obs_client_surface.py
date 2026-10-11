"""Pins what ``ninova_mcp.obs_client`` exposes: names, methods and signatures.

Other modules and tests import these names from this module and replace
methods on client instances, so the surface is part of the behaviour.
"""

from __future__ import annotations

import inspect
import types
import unittest

import ninova_mcp.obs_client as obs_client
from ninova_mcp.obs_client import ObsClient, ObsPublicClient

MODULE_NAMES = ['DEFAULT_OBS_BASE_URL', 'JWT_PATH', 'ObsClient', 'ObsError', 'ObsPublicClient',
 'REDACTED', 'STUDENT_HOME', '_EMAIL', '_REDACTION_NOTE', '_SEASON_WORDS', '_SENSITIVE_CONTACT_TYPE', '_SENSITIVE_KEY',
 '_TERM_FILLER_WORDS', '_resolve_semester_by_name', '_semester_lookup_text', '_semester_recency',
 '_semester_tokens', 'describe_semester', 'redact_obs_profile']
FUNCTION_SIGNATURES = {'_resolve_semester_by_name': "(items: 'list[dict[str, Any]]', semester: 'Any', target_raw: 'str') -> "
                              "'dict[str, Any]'",
 '_semester_lookup_text': "(semester: 'Any') -> 'str'",
 '_semester_recency': "(indexed: 'tuple[int, dict[str, Any]]') -> 'tuple[int, int]'",
 '_semester_tokens': "(value: 'Any') -> 'list[str]'",
 'describe_semester': "(item: 'Any') -> 'str'",
 'redact_obs_profile': "(payload: 'dict[str, Any]') -> 'dict[str, Any]'"}
OBS_CLIENT_MEMBERS = {'__init__': "(self, ninova_client: 'NinovaClient | None' = None, base_url: 'str | None' = None) -> 'None'",
 '_api_request': "(self, method: 'str', path: 'str', *, params: 'dict[str, Any] | None' = None, json_body: "
                 "'dict[str, Any] | None' = None) -> 'Any'",
 '_get_jwt': "(self, *, force: 'bool' = False) -> 'str'",
 '_headers': "(self) -> 'dict[str, str]'",
 '_looks_like_obs_login_page': "(self, response: 'requests.Response') -> 'bool'",
 '_safe_request': "(self, method: 'str', url: 'str', **kwargs: 'Any') -> 'requests.Response'",
 'api_get': "(self, path: 'str', *, params: 'dict[str, Any] | None' = None) -> 'Any'",
 'default_program_id': "(self) -> 'int'",
 'ensure_ready': "(self) -> 'dict[str, Any]'",
 'get_academic_status': "(self, program_id: 'int | str') -> 'dict[str, Any]'",
 'get_advisor': "(self) -> 'dict[str, Any]'",
 'get_attendance': "(self, class_id: 'int | str') -> 'dict[str, Any]'",
 'get_card_info': "(self) -> 'dict[str, Any]'",
 'get_contacts': "(self) -> 'dict[str, Any]'",
 'get_debts': "(self, program_id: 'int | str') -> 'dict[str, Any]'",
 'get_final_calendar': "(self, academic_semester_id: 'int | str') -> 'dict[str, Any]'",
 'get_graduation_remaining': "(self, program_id: 'int | str') -> 'dict[str, Any]'",
 'get_internships': "(self) -> 'dict[str, Any]'",
 'get_lesson_registration_status': "(self) -> 'dict[str, Any]'",
 'get_letter_grades': "(self, academic_semester_id: 'int | str') -> 'dict[str, Any]'",
 'get_midterm_grades': "(self, class_id: 'int | str') -> 'dict[str, Any]'",
 'get_profile': "(self) -> 'dict[str, Any]'",
 'get_registration_draft': "(self) -> 'dict[str, Any]'",
 'get_registration_status': "(self) -> 'dict[str, Any]'",
 'get_schedule': "(self, academic_semester_id: 'int | str') -> 'dict[str, Any]'",
 'get_transcript_preview': "(self, *, english: 'bool' = False) -> 'dict[str, Any]'",
 'list_programs': "(self) -> 'dict[str, Any]'",
 'list_registered_courses': "(self, academic_semester_id: 'int | str') -> 'dict[str, Any]'",
 'list_semesters': "(self) -> 'dict[str, Any]'",
 'ninova': 'property',
 'resolve_semester': "(self, semester: 'str | int | None' = None) -> 'dict[str, Any]'",
 'save_registration_draft': "(self, crns: 'list[str]', *, allow_replace: 'bool' = False, confirm_write: "
                            "'Callable[[dict[str, Any]], dict[str, Any] | None] | None' = None) -> "
                            "'dict[str, Any]'",
 'save_transcript_pdf': "(self, output_dir: 'str | Path', *, english: 'bool' = False, filename: 'str | None' "
                        "= None) -> 'dict[str, Any]'",
 'session': 'property',
 'validate_registration_crns': "(self, crns: 'list[str]') -> 'dict[str, Any]'"}
OBS_PUBLIC_CLIENT_MEMBERS = {'__init__': "(self, session: 'requests.Session | None' = None, base_url: 'str | None' = None, cache_ttl: "
             "'float | None' = None) -> 'None'",
 '_build_course_index': "(self) -> 'dict[str, int]'",
 '_calendar_page': "(self, language: 'str', year_id: 'str | None') -> 'dict[str, Any]'",
 '_get_html': "(self, path: 'str', *, params: 'dict[str, Any] | None' = None) -> 'tuple[str, str]'",
 '_normalize_program_type': "classmethod(cls, raw: 'str') -> 'str'",
 '_post_json': "(self, path: 'str', *, data: 'dict[str, Any]') -> 'Any'",
 '_resolve_department': "(self, program_type: 'str', department: 'str') -> 'int'",
 '_safe_request': "(self, method: 'str', url: 'str', **kwargs: 'Any') -> 'requests.Response'",
 '_throttle': "(self) -> 'None'",
 '_validate_url': "(self, url: 'str') -> 'None'",
 'get_academic_calendar': "(self, date_from: 'str | None' = None, date_to: 'str | None' = None) -> "
                          "'dict[str, Any]'",
 'get_active_semester': "(self, program_type: 'str') -> 'str'",
 'get_branch_prerequisites': "(self, branch: 'str') -> 'dict[str, Any]'",
 'get_course_info_public': "(self, course_code: 'str') -> 'dict[str, Any]'",
 'get_course_schedule': "(self, program_type: 'str', department: 'str') -> 'dict[str, Any]'",
 'get_course_schedule_by_crn': "(self, program_type: 'str', department: 'str', crn: 'str') -> 'dict[str, "
                               "Any]'",
 'get_degree_plan': "(self, plan_id: 'int | str') -> 'dict[str, Any]'",
 'get_postrequisites': "(self, course_id: 'int | str', progress: 'Callable[[float, float | None, str | "
                       "None], None] | None' = None) -> 'dict[str, Any]'",
 'get_prerequisite_detail': "(self, brans_kodu: 'str', ders_no: 'str') -> 'dict[str, Any]'",
 'get_prerequisites': "(self, course_id: 'int | str') -> 'dict[str, Any]'",
 'list_degree_faculties': "(self) -> 'list[dict[str, Any]]'",
 'list_degree_plans': "(self, *, faculty_id: 'int', program_code: 'str', plan_type: 'str' = 'lisans') -> "
                      "'dict[str, Any]'",
 'list_degree_programs': "(self, faculty_id: 'int', plan_type: 'str' = 'lisans') -> 'list[dict[str, Any]]'",
 'list_departments': "(self, program_type: 'str') -> 'list[dict[str, Any]]'",
 'resolve_course_code': "(self, course_code: 'str') -> 'dict[str, Any]'",
 'search_courses': "(self, query: 'str') -> 'list[dict[str, Any]]'",
 'session': 'property'}


def members(cls: type) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in sorted(dir(cls)):
        if name.startswith("__") and name != "__init__":
            continue
        raw = inspect.getattr_static(cls, name)
        if isinstance(raw, property):
            found[name] = "property"
        elif isinstance(raw, classmethod):
            found[name] = "classmethod" + str(inspect.signature(raw.__func__))
        elif isinstance(raw, types.FunctionType):
            found[name] = str(inspect.signature(raw))
    return found


class ModuleSurfaceTests(unittest.TestCase):
    def test_every_name_is_still_importable_from_the_module(self) -> None:
        for name in MODULE_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(obs_client, name))

    def test_module_level_function_signatures(self) -> None:
        for name, signature in FUNCTION_SIGNATURES.items():
            with self.subTest(name=name):
                self.assertEqual(str(inspect.signature(getattr(obs_client, name))), signature)

    def test_time_module_is_reachable_for_tests_that_patch_the_clock_through_it(self) -> None:
        import time

        self.assertIs(obs_client.time, time)

    def test_clients_report_this_module_as_their_home(self) -> None:
        self.assertEqual(ObsClient.__module__, "ninova_mcp.obs_client")
        self.assertEqual(ObsPublicClient.__module__, "ninova_mcp.obs_client")
        self.assertEqual((ObsClient.__name__, ObsPublicClient.__name__), ("ObsClient", "ObsPublicClient"))

    def test_the_app_seam_builds_these_classes(self) -> None:
        import ninova_mcp.server as server
        from ninova_mcp.app_base import server_namespace

        self.assertIs(server.ObsClient, ObsClient)
        self.assertIs(server.ObsPublicClient, ObsPublicClient)
        self.assertIs(server_namespace().ObsClient, ObsClient)
        self.assertIs(server_namespace().ObsPublicClient, ObsPublicClient)


class MovedNameTests(unittest.TestCase):
    """The names live in other modules now; this module hands out the same objects."""

    HOMES = {
        "obs_errors": ("ObsError",),
        "obs_session": ("DEFAULT_OBS_BASE_URL", "JWT_PATH", "STUDENT_HOME"),
        "obs_semesters": ("_SEASON_WORDS", "_TERM_FILLER_WORDS", "_resolve_semester_by_name", "_semester_lookup_text",
                          "_semester_recency", "_semester_tokens", "describe_semester"),
        "obs_redaction": ("REDACTED", "_EMAIL", "_REDACTION_NOTE", "_SENSITIVE_CONTACT_TYPE", "_SENSITIVE_KEY",
                          "redact_obs_profile"),
    }

    def test_reexported_names_are_the_same_objects(self) -> None:
        import importlib

        for module_name, names in self.HOMES.items():
            module = importlib.import_module(f"ninova_mcp.{module_name}")
            for name in names:
                with self.subTest(name=name):
                    self.assertIs(getattr(obs_client, name), getattr(module, name))
        self.assertEqual(sorted(name for names in self.HOMES.values() for name in names),
                         sorted(set(MODULE_NAMES) - {"ObsClient", "ObsPublicClient"}))

    def test_error_raised_by_any_part_is_the_one_callers_catch(self) -> None:
        from unittest.mock import MagicMock, Mock

        from ninova_mcp.obs_client import ObsError

        client = ObsClient(ninova_client=MagicMock())
        client.list_semesters = Mock(return_value={})  # type: ignore[method-assign]
        client.list_programs = Mock(return_value={})  # type: ignore[method-assign]
        public = ObsPublicClient()
        for failing in (client.resolve_semester, client.default_program_id,
                        lambda: public._validate_url("https://example.org/"),
                        lambda: public.get_degree_plan("x"), lambda: public._normalize_program_type("x"),
                        lambda: public.get_prerequisites("x"), lambda: public._calendar_page("en", "x"),
                        lambda: ObsClient(ninova_client=MagicMock(), base_url="https://example.org")):
            with self.assertRaises(ObsError):
                failing()

    def test_the_patched_clock_is_the_one_the_registration_check_reads(self) -> None:
        # test_registration_pacing patches ninova_mcp.obs_client.time.monotonic
        # and .sleep; the pacing code lives in another module and must see them.
        from unittest.mock import MagicMock, patch

        client = ObsClient(ninova_client=MagicMock())
        client._api_request = MagicMock(return_value={"statusCode": 0, "ecrnResultList": []})  # type: ignore[method-assign]
        client._last_registration_check = 100.0
        with patch("ninova_mcp.obs_client.time.monotonic", return_value=100.5) as monotonic, \
                patch("ninova_mcp.obs_client.time.sleep") as sleep:
            client.validate_registration_crns(["10001"])
        sleep.assert_called_once_with(1.5)
        self.assertEqual(monotonic.call_count, 2)
        self.assertEqual(client._last_registration_check, 100.5)


class ClientSurfaceTests(unittest.TestCase):
    # New private helpers may appear; nothing listed here may go or change.
    def test_obs_client_methods_and_signatures(self) -> None:
        found = members(ObsClient)
        for name, signature in OBS_CLIENT_MEMBERS.items():
            with self.subTest(name=name):
                self.assertEqual(found.get(name), signature)
        self.assertEqual({name for name in found if not name.startswith("_")},
                         {name for name in OBS_CLIENT_MEMBERS if not name.startswith("_")})

    def test_obs_public_client_methods_and_signatures(self) -> None:
        found = members(ObsPublicClient)
        for name, signature in OBS_PUBLIC_CLIENT_MEMBERS.items():
            with self.subTest(name=name):
                self.assertEqual(found.get(name), signature)
        self.assertEqual({name for name in found if not name.startswith("_")},
                         {name for name in OBS_PUBLIC_CLIENT_MEMBERS if not name.startswith("_")})

    def test_a_method_replaced_on_an_instance_is_the_one_its_callers_use(self) -> None:
        # Tests across the suite replace fetch methods on an instance; every
        # caller inside the client has to reach them through the instance.
        from unittest.mock import MagicMock, Mock

        client = ObsClient(ninova_client=MagicMock())
        client._api_request = Mock(return_value={"ok": 1})  # type: ignore[method-assign]
        self.assertEqual(client.get_profile(), {"ok": 1})
        client.api_get = Mock(return_value={"ogrenciDonemListesi": [{"akademikDonemId": 1}]})  # type: ignore[method-assign]
        self.assertEqual(client.resolve_semester(), {"akademikDonemId": 1})
        public = ObsPublicClient()
        public._get_html = Mock(return_value=('[{"bransKoduId": 3, "dersBransKodu": "BLG"}]', "u"))  # type: ignore[method-assign]
        self.assertEqual(public._resolve_department("LS", "BLG"), 3)
        public._safe_request = Mock(side_effect=AssertionError("must go through _get_html"))  # type: ignore[method-assign]
        self.assertEqual(public.list_departments("LS"), [{"brans_kodu_id": 3, "code": "BLG"}])


if __name__ == "__main__":
    unittest.main()
