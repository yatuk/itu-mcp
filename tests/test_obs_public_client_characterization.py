"""Pins the behaviour of ``ObsPublicClient`` method by method.

No test reaches the network: requests go to a fake session, and most tests
replace the client's own fetch methods on the instance.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import Mock, call, patch

import requests

from ninova_mcp.obs_client import ObsError, ObsPublicClient

import test_academic_calendar_year as calendar_fixtures

BASE = "https://obs.itu.edu.tr"
BRANCH_URL = BASE + "/public/GenelTanimlamalar/OnsartAra?DersBransKoduId=3"
SELECT_HTML = """
<select id="DersBransKoduId">
  <option value="">Seçiniz</option>
  <option value="304">BBF - Bilgisayar Bilimleri</option>
  <option value="301">MAT - Matematik</option>
  <option value="x9">ZZZ - Sayı olmayan</option>
  <option value="77">İŞL</option>
</select>
"""
DEPARTMENTS = [{"brans_kodu_id": 3, "code": "BLG"}, {"brans_kodu_id": 310, "code": "BBF"},
               {"brans_kodu_id": 12, "code": "İŞL"}, {"brans_kodu_id": 5, "code": None}]


def response(url: str, status: int = 200, body: str = "ok", location: str | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body.encode("utf-8")
    result._content_consumed = True
    result.encoding = "utf-8"
    if location is not None:
        result.headers["Location"] = location
    return result


class FakeSession:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def public(answers: list[Any] | None = None) -> tuple[ObsPublicClient, FakeSession]:
    session = FakeSession(answers or [])
    client = ObsPublicClient(session=session, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    return client, session


def leaf(code: str, grade: str | None = "DD") -> dict[str, Any]:
    return {"type": "course", "code": code, "min_grade": grade}


def rule(tree: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    return {"course_name": "Name", "requirement_tree": tree, "expression": "expr", "minimum_grades": {"X": "DD"},
            "credit_requirement": None, "credit_requirement_text": None, **extra}


class ConstructionTests(unittest.TestCase):
    def test_base_url_must_be_the_obs_host_over_https(self) -> None:
        for bad in ("http://obs.itu.edu.tr", "https://obs.itu.edu.tr.attacker.example", "https://www.takvim.sis.itu.edu.tr"):
            with self.subTest(bad=bad):
                with self.assertRaises(ObsError) as caught:
                    ObsPublicClient(base_url=bad)
                self.assertEqual(str(caught.exception), "NINOVA_OBS_BASE_URL must be https://obs.itu.edu.tr")
        with patch.dict("os.environ", {"NINOVA_OBS_BASE_URL": "https://example.org"}):
            with self.assertRaises(ObsError):
                ObsPublicClient()
        self.assertEqual(ObsPublicClient(base_url=BASE + "/").base_url, BASE)

    def test_cache_lifetimes_come_from_arguments_then_environment_then_defaults(self) -> None:
        clean = {"NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS": "", "NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS": ""}
        with patch.dict("os.environ", clean):
            client = ObsPublicClient()
            self.assertEqual(client._cache.default_ttl_seconds, 3600.0)
            self.assertEqual(client._schedule_cache_ttl, 60.0)
        with patch.dict("os.environ", {"NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS": "5", "NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS": "7"}):
            self.assertEqual(ObsPublicClient()._cache.default_ttl_seconds, 5.0)
            self.assertEqual(ObsPublicClient(cache_ttl=9)._cache.default_ttl_seconds, 9.0)
            self.assertEqual(ObsPublicClient()._schedule_cache_ttl, 7.0)

    def test_initial_state_and_class_constants(self) -> None:
        client = ObsPublicClient()
        self.assertIsNone(client._session)
        self.assertIsNone(client._course_index)
        self.assertEqual(client._last_request_at, 0.0)
        self.assertFalse(hasattr(client, "_dept_cache"))
        self.assertEqual(ObsPublicClient.DEFAULT_BASE_URL, BASE)
        self.assertEqual(ObsPublicClient.DEFAULT_CACHE_TTL, 3600.0)
        self.assertEqual(ObsPublicClient.DEFAULT_SCHEDULE_CACHE_TTL, 60.0)
        self.assertEqual(ObsPublicClient.DEFAULT_REQUEST_DELAY_SECONDS, 0.12)
        self.assertEqual(ObsPublicClient.MAX_CALENDAR_ACADEMIC_YEARS, 2)
        self.assertEqual(ObsPublicClient.ALLOWED_PUBLIC_HOSTS, frozenset({"obs.itu.edu.tr", "www.takvim.sis.itu.edu.tr"}))
        self.assertEqual(ObsPublicClient.ACADEMIC_CALENDAR_URLS, {
            "en": "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/EN/academic-calendar/index.php",
            "tr": "https://www.takvim.sis.itu.edu.tr/AkademikTakvim/TR/akademik-takvim/index.php",
        })
        self.assertEqual(ObsPublicClient.VALID_PLAN_TYPES, {
            "on-lisans", "lisans", "cap", "yandal", "muhendislik-tamamlama", "bilimsel-hazirlik", "yuksek-lisans",
            "tezsiz-yuksek-lisans", "yuksek-lisans-ikinci-ogretim", "doktora", "uolp",
        })
        self.assertEqual(set(ObsPublicClient.PROGRAM_TYPE_MAP.values()), {"LS", "LU", "ÖL", "LUİ"})
        self.assertEqual(len(ObsPublicClient.PROGRAM_TYPE_MAP), 15)

    def test_a_session_is_created_on_first_use_with_the_default_headers(self) -> None:
        client = ObsPublicClient()
        session = client.session
        self.assertIs(client.session, session)
        self.assertIn("User-Agent", session.headers)
        self.assertTrue(session.headers["Accept-Language"])
        given = requests.Session()
        self.assertIs(ObsPublicClient(session=given).session, given)


class TransportTests(unittest.TestCase):
    def test_only_https_urls_on_the_two_public_hosts_pass(self) -> None:
        client, _ = public()
        for good in (BASE + "/public/x", "https://OBS.itu.edu.tr/", "https://www.takvim.sis.itu.edu.tr/a?b=1"):
            client._validate_url(good)
        for bad in ("http://obs.itu.edu.tr/public/x", "https://ninova.itu.edu.tr/", "https://obs.itu.edu.tr.example.org/",
                    "https://takvim.sis.itu.edu.tr/", "ftp://obs.itu.edu.tr/", "//obs.itu.edu.tr/", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ObsError) as caught:
                    client._validate_url(bad)
                self.assertEqual(str(caught.exception), "OBS public request destination is not allowlisted")

    def test_get_html_normalises_the_path_and_returns_text_and_final_url(self) -> None:
        url = BASE + "/public/x"
        client, session = public([response(url + "?a=1", body="<p>ğ</p>")])
        client._throttle = Mock()  # type: ignore[method-assign]
        self.assertEqual(client._get_html("public/x", params={"a": 1}), ("<p>ğ</p>", url + "?a=1"))
        client._throttle.assert_called_once_with()
        method, sent, kwargs = session.calls[0]
        self.assertEqual((method, sent), ("GET", url))
        self.assertEqual(kwargs, {"allow_redirects": False, "params": {"a": 1}, "timeout": 30})

    def test_get_html_guesses_the_encoding_when_the_server_names_none_or_latin1(self) -> None:
        for declared in (None, "ISO-8859-1", "iso-8859-1"):
            with self.subTest(declared=declared):
                answer = response(BASE + "/public/x")
                answer._content = "çğış öü Dönem".encode("utf-8")
                answer.encoding = declared
                client, _ = public([answer])
                self.assertEqual(client._get_html("/public/x")[0], "çğış öü Dönem")

    def test_http_errors_name_the_path(self) -> None:
        for status in (400, 404, 503):
            client, _ = public([response(BASE + "/public/x", status), response(BASE + "/public/y", status)])
            with self.assertRaises(ObsError) as caught:
                client._get_html("public/x")
            self.assertEqual(str(caught.exception), f"OBS public endpoint /public/x returned HTTP {status}")
            with self.assertRaises(ObsError) as caught:
                client._post_json("public/y", data={})
            self.assertEqual(str(caught.exception), f"OBS public endpoint /public/y returned HTTP {status}")

    def test_post_json_sends_form_data_and_decodes_the_answer(self) -> None:
        url = BASE + "/public/y"
        client, session = public([response(url, body='[{"a": 1}]'), response(url, body="<html>")])
        client._throttle = Mock()  # type: ignore[method-assign]
        self.assertEqual(client._post_json("public/y", data={"k": "v"}), [{"a": 1}])
        self.assertEqual(session.calls[0], ("POST", url, {"allow_redirects": False, "data": {"k": "v"}, "timeout": 30}))
        with self.assertRaises(ObsError) as caught:
            client._post_json("/public/y", data={})
        self.assertEqual(str(caught.exception), "OBS public endpoint /public/y did not return JSON")
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(client._throttle.call_count, 2)

    def test_a_request_outside_the_allowlist_is_refused_before_it_is_sent(self) -> None:
        client, session = public()
        with self.assertRaises(ObsError) as caught:
            client._safe_request("GET", "https://example.org/x")
        self.assertEqual(str(caught.exception), "OBS public request destination is not allowlisted")
        self.assertEqual(session.calls, [])

    def test_a_redirect_is_followed_only_to_an_allowlisted_host(self) -> None:
        url = BASE + "/public/x"
        client, session = public([response(url, 302, location="https://example.org/steal")])
        with self.assertRaises(ObsError) as caught:
            client._get_html("/public/x")
        self.assertEqual(str(caught.exception), "OBS public request destination is not allowlisted")
        self.assertEqual(len(session.calls), 1)
        other = "https://www.takvim.sis.itu.edu.tr/next"
        client, session = public([response(url, 302, location=other), response(other, body="moved")])
        self.assertEqual(client._get_html("/public/x"), ("moved", other))
        self.assertEqual([item[1] for item in session.calls], [url, other])

    def test_transport_failures_become_a_safe_error(self) -> None:
        client, _ = public([requests.ConnectionError("down")])
        with self.assertRaises(ObsError) as caught:
            client._get_html("/public/x")
        self.assertEqual(str(caught.exception), "OBS public request failed safely: down")
        self.assertIsInstance(caught.exception.__cause__, requests.ConnectionError)

    def test_throttle_waits_for_the_rest_of_the_interval(self) -> None:
        client, _ = public()
        namespace = ObsPublicClient._throttle.__globals__["time"]
        with patch.object(namespace, "monotonic", side_effect=[100.0, 100.5]), patch.object(namespace, "sleep") as sleep:
            client._throttle()
            sleep.assert_not_called()
        client._min_request_interval = 0.5
        client._last_request_at = 100.0
        with patch.object(namespace, "monotonic", side_effect=[100.2, 100.5]), patch.object(namespace, "sleep") as sleep:
            client._throttle()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.3)
        self.assertEqual(client._last_request_at, 100.5)
        with patch.object(namespace, "monotonic", side_effect=[101.0, 101.0]), patch.object(namespace, "sleep") as sleep:
            client._throttle()
        sleep.assert_not_called()
        self.assertEqual(client._last_request_at, 101.0)


class CourseIndexTests(unittest.TestCase):
    def test_index_maps_branch_codes_and_raw_ids_and_is_cached(self) -> None:
        client, _ = public()
        client._get_html = Mock(return_value=(SELECT_HTML, BASE + "/x"))  # type: ignore[method-assign]
        index = client._build_course_index()
        self.assertEqual(index, {"bbf": 304, "304": 304, "mat": 301, "301": 301, "isl": 77, "77": 77})
        self.assertEqual(list(index), ["bbf", "304", "mat", "301", "isl", "77"])
        self.assertIs(client._course_index, index)
        self.assertIs(client._build_course_index(), index)
        client._get_html.assert_called_once_with("/public/GenelTanimlamalar/DersOnsartList")


class SearchAndCodeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, _ = public()
        self.client._build_course_index = Mock(return_value={"b": 1, "bbf": 304, "304": 304, "bb": 2})  # type: ignore[method-assign]

    def test_search_needs_a_full_code_and_recognisable_results(self) -> None:
        self.client._get_html = Mock(return_value=("<html></html>", BASE + "/x"))  # type: ignore[method-assign]
        with self.assertRaises(ObsError) as caught:
            self.client.search_courses("BLG")
        self.assertEqual(str(caught.exception), "A full course code is required for an OBS catalog lookup.")
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.client._get_html.assert_not_called()
        with self.assertRaises(ObsError) as caught:
            self.client.search_courses("blg223e")
        self.assertEqual(str(caught.exception), "OBS did not return recognizable course information for this code.")
        self.client._get_html.assert_called_once_with(
            "/public/DersBilgi/DersBilgiSearch", params={"bransKodu": "BLG", "dersNo": "223E"})

    def test_search_puts_the_exact_code_first_and_keeps_the_rest_in_order(self) -> None:
        rows = [{"code": "BLG 223"}, {"code": "BLG 223E"}, {"code": "BLG 223EL"}, {}]
        self.client._get_html = Mock(return_value=("<html></html>", BASE + "/x"))  # type: ignore[method-assign]
        with patch.dict(ObsPublicClient.search_courses.__globals__, {"extract_course_search_results": Mock(return_value=rows)}):
            result = self.client.search_courses("BLG 223E")
        self.assertEqual(result, [{"code": "BLG 223E"}, {"code": "BLG 223"}, {"code": "BLG 223EL"}, {}])

    def test_branch_code_resolves_from_the_index(self) -> None:
        self.assertEqual(self.client.resolve_course_code("  bbf "),
                         {"code": "BBF", "brans_kodu_id": 304, "source": "dept_index"})
        self.assertEqual(self.client.resolve_course_code("304"),
                         {"code": "304", "brans_kodu_id": 304, "source": "dept_index"})

    def test_course_code_resolves_by_the_first_index_key_of_two_or_more_characters_it_starts_with(self) -> None:
        self.assertEqual(self.client.resolve_course_code("bbf 201e"), {
            "code": "BBF 201E", "brans_kodu_id": 304, "matched_key": "bbf", "source": "dept_index_prefix"})
        self.assertEqual(self.client.resolve_course_code("BBX 1")["matched_key"], "bb")
        # Raw ids are index keys too, so a number starting with one resolves the same way.
        self.assertEqual(self.client.resolve_course_code("30412")["matched_key"], "304")

    def test_catalog_fallback_prefers_the_exact_code(self) -> None:
        rows = [{"code": "MAT 101", "name": "Math", "url": "u1"}, {"code": "MAT 101E", "name": "Math E", "url": "u2"}]
        self.client.search_courses = Mock(return_value=rows)  # type: ignore[method-assign]
        self.assertEqual(self.client.resolve_course_code(" mat 101e "), {
            "code": "MAT 101E", "name": "Math E", "brans_kodu_id": None, "url": "u2", "source": "search_exact"})
        self.client.search_courses.assert_called_once_with("mat 101e")
        self.assertEqual(self.client.resolve_course_code("mat 102"), {
            "code": "MAT 101", "name": "Math", "brans_kodu_id": None, "url": "u1", "source": "search_fuzzy"})
        self.client.search_courses = Mock(return_value=[{}])  # type: ignore[method-assign]
        self.assertEqual(self.client.resolve_course_code("mat 102"), {
            "code": "MAT 102", "name": None, "brans_kodu_id": None, "url": None, "source": "search_fuzzy"})

    def test_catalog_fallback_without_results(self) -> None:
        self.client.search_courses = Mock(return_value=[])  # type: ignore[method-assign]
        with self.assertRaises(ObsError) as caught:
            self.client.resolve_course_code(" mat 101 ")
        self.assertEqual(str(caught.exception), "Course not found in OBS public catalog: mat 101. "
                                                "Try a different code format (e.g. 'BBF 201E' vs 'BBF201E').")
        self.client.search_courses = Mock(side_effect=ObsError("catalog down"))  # type: ignore[method-assign]
        with self.assertRaisesRegex(ObsError, "catalog down"):
            self.client.resolve_course_code("mat 101")

    def test_course_info_picks_exact_then_first_containing_then_first(self) -> None:
        rows = [{"code": "MAT 101EL"}, {"code": "MAT 101E"}, {"code": "FIZ 101"}]
        self.client.search_courses = Mock(return_value=rows)  # type: ignore[method-assign]
        self.assertEqual(self.client.get_course_info_public("mat 101e"),
                         {"query": "mat 101e", "found": True, "course": {"code": "MAT 101E"}, "all_results_count": 3})
        self.assertEqual(self.client.get_course_info_public("MAT 101")["course"], {"code": "MAT 101EL"})
        self.assertEqual(self.client.get_course_info_public("KIM 101")["course"], {"code": "MAT 101EL"})
        self.client.search_courses = Mock(return_value=[])  # type: ignore[method-assign]
        self.assertEqual(self.client.get_course_info_public("KIM 101"),
                         {"query": "KIM 101", "found": False, "course": None, "all_results_count": 0})


class PrerequisiteDetailTests(unittest.TestCase):
    EMPTY = {
        "course_id": "BLG 223E", "course_code": "BLG 223E", "brans_kodu": "BLG", "ders_no": "223E", "title": "BLG 223E",
        "prerequisites": [], "requirement_tree": None, "prerequisite_status": "unknown", "available": False,
        "parse_warnings": None, "raw_tables": [], "text_excerpt": "", "untrusted_external_content": True,
    }

    def setUp(self) -> None:
        self.client, _ = public()
        self.client.search_courses = Mock(side_effect=AssertionError("catalog must not be read"))  # type: ignore[method-assign]

    def branch(self, rules: dict[str, Any], **extra: Any) -> Mock:
        self.client.get_branch_prerequisites = Mock(  # type: ignore[method-assign]
            return_value={"rules": rules, "table_parsed": True, "url": BRANCH_URL, **extra})
        return self.client.get_branch_prerequisites

    def test_invalid_code(self) -> None:
        self.client.get_branch_prerequisites = Mock()  # type: ignore[method-assign]
        for branch, number in (("BLG", "x"), ("", ""), ("BLG 223E", "223E")):
            with self.subTest(branch=branch, number=number):
                with self.assertRaises(ObsError) as caught:
                    self.client.get_prerequisite_detail(branch, number)
                self.assertEqual(str(caught.exception),
                                 "A valid branch and course number are required, such as BLG and 223E.")
                self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.client.get_branch_prerequisites.assert_not_called()

    def test_rule_with_courses(self) -> None:
        tree = {"type": "or", "operands": [leaf("BLG 102"), leaf("BLG 102", "DD"), leaf("BLG 102", "CC"),
                                           {"type": "and", "operands": [leaf("BIL 104E", None)]}]}
        found = rule(tree, credit_requirement=60.0, credit_requirement_text="60,00")
        read = self.branch({"BLG 223E": found})
        result = self.client.get_prerequisite_detail("blg", "223e")
        self.assertEqual(result, {
            **self.EMPTY,
            "prerequisites": [
                {"code": "BLG 102", "name": None, "min_grade": "DD", "type": "prerequisite_reference", "group": None},
                {"code": "BLG 102", "name": None, "min_grade": "CC", "type": "prerequisite_reference", "group": None},
                {"code": "BIL 104E", "name": None, "min_grade": None, "type": "prerequisite_reference", "group": None},
            ],
            "requirement_tree": tree,
            "prerequisite_status": "has_prerequisites",
            "available": True,
            "text_excerpt": "expr",
            "url": BRANCH_URL,
            "prerequisite_source": BRANCH_URL,
            "official_prerequisite": found,
            "credit_requirement": 60.0,
            "credit_requirement_text": "60,00",
            "prerequisite_status_note": (
                "The prerequisite list contains references, not a list of courses that are all mandatory. "
                "Use the full requirement_tree for AND/OR alternatives and minimum grades."
            ),
        })
        self.assertEqual(list(result), [*self.EMPTY, "url", "prerequisite_source", "official_prerequisite",
                                        "credit_requirement", "credit_requirement_text", "prerequisite_status_note"])
        read.assert_called_once_with("BLG")
        self.assertIs(self.client.get_prerequisite_detail("BLG", "223E"), result)
        read.assert_called_once()

    def test_rule_with_a_credit_requirement_only(self) -> None:
        self.branch({"BLG 223E": rule(None, credit_requirement=95.0, expression=None)})
        result = self.client.get_prerequisite_detail("BLG", "223E")
        self.assertEqual((result["prerequisite_status"], result["available"], result["prerequisites"], result["text_excerpt"]),
                         ("has_prerequisites", True, [], ""))

    def test_rule_without_courses_or_credits_is_available_but_unknown_and_not_cached(self) -> None:
        read = self.branch({"BLG 223E": rule(None)})
        result = self.client.get_prerequisite_detail("BLG", "223E")
        self.assertEqual((result["prerequisite_status"], result["available"]), ("unknown", True))
        self.assertIn("not a list of courses that are all mandatory", result["prerequisite_status_note"])
        self.client.get_prerequisite_detail("BLG", "223E")
        self.assertEqual(read.call_count, 2)

    def test_unreadable_branch(self) -> None:
        self.client.get_branch_prerequisites = Mock(side_effect=ObsError("table down"))  # type: ignore[method-assign]
        result = self.client.get_prerequisite_detail("BLG", "223E")
        self.assertEqual(result, {**self.EMPTY, "prerequisite_status_note": (
            "The official branch prerequisite table could not be read: table down. "
            "An empty result is not evidence of no prerequisites.")})
        self.client.get_prerequisite_detail("BLG", "223E")
        self.assertEqual(self.client.get_branch_prerequisites.call_count, 2)

    def test_course_missing_from_a_parsed_table_and_confirmed_by_the_catalog(self) -> None:
        read = self.branch({})
        self.client.search_courses = Mock(return_value=[{"code": "BLG 223"}, {"code": "BLG 223E"}])  # type: ignore[method-assign]
        result = self.client.get_prerequisite_detail("BLG", "223E")
        self.assertEqual(result, {
            **self.EMPTY, "prerequisite_status": "no_prerequisites", "available": True,
            "url": BRANCH_URL, "prerequisite_source": BRANCH_URL,
            "prerequisite_status_note": (
                "BLG 223E exists in the official catalog and has no entry in the parsed BLG prerequisite table."),
        })
        self.client.search_courses.assert_called_once_with("BLG 223E")
        self.assertIs(self.client.get_prerequisite_detail("BLG", "223E"), result)
        read.assert_called_once()

    def test_course_missing_from_a_parsed_table_and_not_confirmed(self) -> None:
        expected = {**self.EMPTY, "url": BRANCH_URL, "prerequisite_source": BRANCH_URL, "prerequisite_status_note": (
            "The course is absent from the prerequisite table, but its exact catalog identity could not be verified.")}
        for catalog in ({"return_value": []}, {"return_value": [{"code": "BLG 223"}]}, {"side_effect": ObsError("down")}):
            with self.subTest(catalog=catalog):
                self.branch({})
                self.client.search_courses = Mock(**catalog)  # type: ignore[method-assign]
                self.assertEqual(self.client.get_prerequisite_detail("BLG", "223E"), expected)

    def test_table_that_could_not_be_parsed(self) -> None:
        self.client.get_branch_prerequisites = Mock(return_value={"table_parsed": False})  # type: ignore[method-assign]
        self.assertEqual(self.client.get_prerequisite_detail("BLG", "223E"), {
            **self.EMPTY, "url": None, "prerequisite_source": None, "prerequisite_status_note": (
                "The official branch prerequisite table could not be parsed; an empty result is not evidence of no prerequisites."),
        })

    def test_get_prerequisites_routes_codes_and_rejects_partial_ones(self) -> None:
        self.client.get_prerequisite_detail = Mock(return_value={"d": 1})  # type: ignore[method-assign]
        self.assertEqual(self.client.get_prerequisites(" blg223e "), {"d": 1})
        self.client.get_prerequisite_detail.assert_called_once_with("BLG", "223E")
        for bad in ("BLG", "", "٣", "BLG 22"):
            with self.subTest(bad=bad):
                with self.assertRaises(ObsError) as caught:
                    self.client.get_prerequisites(bad)
                self.assertEqual(str(caught.exception), "A full course code such as BLG 223E is required.")

    def test_numeric_id_reads_the_branch_table_and_says_it_is_branch_scoped(self) -> None:
        parsed = {"prerequisites": [], "raw_tables": [["x"]], "title": "t"}
        self.client._get_html = Mock(return_value=("<html></html>", BRANCH_URL))  # type: ignore[method-assign]
        with patch.dict(ObsPublicClient.get_prerequisites.__globals__, {"extract_prerequisite_list": Mock(return_value=dict(parsed))}) as names:
            result = self.client.get_prerequisites(" 3 ")
            names["extract_prerequisite_list"].assert_called_once_with("<html></html>", BRANCH_URL, base_url=BASE)
            names["extract_prerequisite_list"].return_value = {"prerequisites": [], "raw_tables": []}
            self.assertFalse(self.client.get_prerequisites(3)["available"])
        self.client._get_html.assert_called_with("/public/GenelTanimlamalar/OnsartAra", params={"DersBransKoduId": "3"})
        self.assertEqual(result, {
            **parsed, "course_id": "3", "scope": "branch", "available": True, "prerequisite_status": "unknown",
            "prerequisite_status_note": "A branch ID cannot identify a course's prerequisites. Supply the full course code.",
        })


class BranchPrerequisiteTests(unittest.TestCase):
    def test_branch_table_is_read_by_id_parsed_and_cached(self) -> None:
        client, _ = public()
        client._build_course_index = Mock(return_value={"isl": 77})  # type: ignore[method-assign]
        client._get_html = Mock(return_value=("<html></html>", BRANCH_URL))  # type: ignore[method-assign]
        with patch("ninova_mcp.prerequisites.extract_branch_prerequisites", return_value={"rules": {}}) as extract:
            result = client.get_branch_prerequisites(" işl ")
            self.assertIs(client.get_branch_prerequisites("IŞL"), result)
        self.assertEqual(result, {"rules": {}, "brans_kodu_id": 77})
        # The table is parsed and cached under the plainly upper-cased text.
        extract.assert_called_once_with("<html></html>", BRANCH_URL, "IŞL")
        self.assertIs(client._cache.get("branch_prereq:IŞL"), result)
        client._get_html.assert_called_once_with("/public/GenelTanimlamalar/OnsartAra", params={"DersBransKoduId": 77})

    def test_unknown_branch(self) -> None:
        client, _ = public()
        client._build_course_index = Mock(return_value={"blg": 3})  # type: ignore[method-assign]
        with self.assertRaises(ObsError) as caught:
            client.get_branch_prerequisites(" xyz ")
        self.assertEqual(str(caught.exception), "Branş kodu OBS önşart listesinde bulunamadı: ' xyz '")


class PostrequisiteTests(unittest.TestCase):
    NOTE = ("These courses reference the selected course in their prerequisite expressions. "
            "Other alternatives and minimum grades still apply. ")
    COMPLETE = NOTE + "All listed branches were checked."
    INCOMPLETE = NOTE + "Coverage is incomplete; an empty result does not prove no postrequisites."

    def client(self, index: dict[str, int], branches: Any) -> ObsPublicClient:
        client, _ = public()
        client._build_course_index = Mock(return_value=index)  # type: ignore[method-assign]
        if isinstance(branches, dict):
            def read(branch: str) -> dict[str, Any]:
                answer = branches[branch]
                if isinstance(answer, Exception):
                    raise answer
                return answer
            client.get_branch_prerequisites = Mock(side_effect=read)  # type: ignore[method-assign]
        else:
            client.get_branch_prerequisites = Mock(side_effect=branches)  # type: ignore[method-assign]
        return client

    def parsed(self, rules: dict[str, Any], url: str | None = BRANCH_URL) -> dict[str, Any]:
        return {"rules": rules, "table_parsed": True, "url": url}

    def test_invalid_code_is_rejected_before_anything_is_read(self) -> None:
        client = self.client({}, {})
        with self.assertRaises(ObsError) as caught:
            client.get_postrequisites("BLG")
        self.assertEqual(str(caught.exception), "A full course code is required for postrequisite lookup.")
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        client._build_course_index.assert_not_called()

    def test_full_result_of_a_partly_failed_scan(self) -> None:
        tree = {"type": "or", "operands": [leaf("BLG 102E"), leaf("BLG 102E", "CC"), leaf("BLG 102")]}
        match = rule(tree, credit_requirement=60.0, credit_requirement_text="60,00")
        client = self.client({"mat": 7, "blg": 3, "fiz": 9, "3": 3, "7": 7, "kim": 4}, {
            "BLG": self.parsed({"BLG 335E": match, "BLG 223E": rule({"type": "and", "operands": [leaf("BLG 102E")]}),
                                "BLG 101": rule(leaf("BLG 102")), "BLG 100": rule(None)}),
            "FIZ": ObsError("down"),
            "KIM": {"table_parsed": False, "url": "ignored", "rules": {"KIM 1": rule(leaf("BLG 102E"))}},
            "MAT": self.parsed({"MAT 1": rule(leaf("BLG 102E"), course_name=None)}, url=None),
        })
        result = client.get_postrequisites("blg102e")
        row = {"prerequisite_type": "prerequisite_reference", "group": None, "expression": "expr",
               "minimum_grades": {"X": "DD"}, "credit_requirement": None, "credit_requirement_text": None}
        self.assertEqual(result, {
            "course_id": "BLG 102E",
            "course_code": "BLG 102E",
            "available": True,
            "complete": False,
            "postrequisite_status": "has_postrequisites",
            "postrequisites": [
                {**row, "code": "BLG 223E", "course_id": "BLG 223E", "name": "Name",
                 "requirement_tree": {"type": "and", "operands": [leaf("BLG 102E")]}},
                {**row, "code": "BLG 335E", "course_id": "BLG 335E", "name": "Name", "requirement_tree": tree,
                 "credit_requirement": 60.0, "credit_requirement_text": "60,00"},
                {**row, "code": "MAT 1", "course_id": "MAT 1", "name": None, "requirement_tree": leaf("BLG 102E")},
            ],
            "scanned_courses": 5,
            "scanned_branches": 4,
            "unknown_branches": ["FIZ", "KIM"],
            "unscanned_branches": [],
            "sources": [BRANCH_URL],
            "note": self.INCOMPLETE,
            "untrusted_external_content": True,
        })
        self.assertEqual(list(result), [
            "course_id", "course_code", "available", "complete", "postrequisite_status", "postrequisites",
            "scanned_courses", "scanned_branches", "unknown_branches", "unscanned_branches", "sources", "note",
            "untrusted_external_content"])
        self.assertEqual(list(result["postrequisites"][0]), [
            "code", "course_id", "name", "prerequisite_type", "group", "requirement_tree", "expression",
            "minimum_grades", "credit_requirement", "credit_requirement_text"])
        self.assertEqual(client.get_branch_prerequisites.call_args_list, [call("BLG"), call("FIZ"), call("KIM"), call("MAT")])

    def test_status_follows_matches_and_completeness(self) -> None:
        hit = self.parsed({"BLG 1": rule(leaf("FIZ 101E"))})
        miss = self.parsed({"BLG 1": rule(leaf("FIZ 101"))})
        cases = (
            ({"blg": 3}, {"BLG": hit}, "has_postrequisites", True, True, self.COMPLETE),
            ({"blg": 3}, {"BLG": miss}, "no_postrequisites", True, True, self.COMPLETE),
            ({"blg": 3, "mat": 7}, {"BLG": miss, "MAT": ObsError("down")}, "unknown", False, True, self.INCOMPLETE),
            ({"blg": 3, "mat": 7}, {"BLG": hit, "MAT": ObsError("down")}, "has_postrequisites", False, True, self.INCOMPLETE),
            ({"blg": 3}, {"BLG": self.parsed({}, url=None)}, "no_postrequisites", True, False, self.COMPLETE),
            ({"blg": 3}, {"BLG": self.parsed({}, url="")}, "no_postrequisites", True, False, self.COMPLETE),
            ({"blg": 3}, {"BLG": {"table_parsed": True}}, "no_postrequisites", True, False, self.COMPLETE),
            ({"blg": 3}, {"BLG": {}}, "unknown", False, False, self.INCOMPLETE),
            ({"3": 3}, {}, "unknown", False, False, self.INCOMPLETE),
            ({}, {}, "unknown", False, False, self.INCOMPLETE),
        )
        for index, branches, status, complete, available, note in cases:
            with self.subTest(index=index, branches=branches):
                result = self.client(index, branches).get_postrequisites("FIZ 101E")
                self.assertEqual((result["postrequisite_status"], result["complete"], result["available"], result["note"]),
                                 (status, complete, available, note))

    def test_branch_names_are_upper_cased_merged_and_sorted_and_ids_are_skipped(self) -> None:
        client = self.client({"mat": 7, "blg": 3, "BLG": 3, "12": 1, "isl": 5}, [self.parsed({})] * 3)
        result = client.get_postrequisites("FIZ 101E")
        self.assertEqual(client.get_branch_prerequisites.call_args_list, [call("BLG"), call("ISL"), call("MAT")])
        self.assertEqual(result["scanned_branches"], 3)

    def test_at_most_two_hundred_branches_are_scanned(self) -> None:
        index = {f"b{n:03d}": n for n in range(205)}
        client = self.client(index, [self.parsed({})] * 200)
        progress = Mock()
        result = client.get_postrequisites("FIZ 101E", progress)
        self.assertEqual(client.get_branch_prerequisites.call_count, 200)
        self.assertEqual(result["scanned_branches"], 200)
        self.assertEqual(result["unscanned_branches"], ["B200", "B201", "B202", "B203", "B204"])
        self.assertEqual((result["complete"], result["postrequisite_status"], result["note"]), (False, "unknown", self.INCOMPLETE))
        self.assertEqual(progress.call_count, 201)
        self.assertEqual(progress.call_args_list[199], call(199, 200, "Ön şart tablosu taranıyor (200/200): B199"))
        exact = self.client({f"b{n:03d}": n for n in range(200)}, [self.parsed({})] * 200).get_postrequisites("FIZ 101E")
        self.assertEqual((exact["complete"], exact["unscanned_branches"]), (True, []))

    def test_progress_is_reported_before_each_branch_and_once_at_the_end(self) -> None:
        events: list[Any] = []
        client = self.client({"blg": 3, "mat": 7}, {})
        client.get_branch_prerequisites = Mock(side_effect=lambda branch: events.append(branch) or self.parsed({}))  # type: ignore[method-assign]
        client.get_postrequisites("FIZ 101E", progress=lambda *args: events.append(args))
        self.assertEqual(events, [
            (0, 2, "Ön şart tablosu taranıyor (1/2): BLG"), "BLG",
            (1, 2, "Ön şart tablosu taranıyor (2/2): MAT"), "MAT",
            (2, 2, "Ön şart taraması tamamlandı"),
        ])
        empty: list[Any] = []
        self.client({}, {}).get_postrequisites("FIZ 101E", progress=lambda *args: empty.append(args))
        self.assertEqual(empty, [(0, 0, "Ön şart taraması tamamlandı")])

    def test_a_failing_progress_callback_changes_nothing(self) -> None:
        branches = {"BLG": self.parsed({"BLG 1": rule(leaf("FIZ 101E"))}), "MAT": self.parsed({})}
        quiet = self.client({"blg": 3, "mat": 7}, branches).get_postrequisites("FIZ 101E")
        progress = Mock(side_effect=RuntimeError("client went away"))
        loud = self.client({"blg": 3, "mat": 7}, branches).get_postrequisites("FIZ 101E", progress)
        self.assertEqual(loud, quiet)
        self.assertEqual(progress.call_count, 3)

    def test_result_is_cached_per_normalised_code_and_a_cached_scan_reports_no_progress(self) -> None:
        client = self.client({"blg": 3}, [self.parsed({})])
        first = client.get_postrequisites("fiz101e")
        progress = Mock()
        self.assertIs(client.get_postrequisites("FIZ 101E", progress), first)
        progress.assert_not_called()
        client._build_course_index.assert_called_once()
        self.assertIs(client._cache.get("postreq:FIZ 101E"), first)


class ProgramTypeAndDepartmentTests(unittest.TestCase):
    def test_program_type_spellings(self) -> None:
        for raw, expected in (("ls", "LS"), ("Lisans", "LS"), ("LİSANS", "LS"), ("lu", "LU"), ("Yüksek Lisans", "LU"),
                              ("yuksek lisans", "LU"), ("ÖL", "ÖL"), ("onlisans", "ÖL"), ("LUİ", "LUİ"), ("lui", "LUİ"),
                              ("Lisansüstü 2", "LUİ")):
            with self.subTest(raw=raw):
                self.assertEqual(ObsPublicClient._normalize_program_type(raw), expected)

    def test_unknown_program_type(self) -> None:
        with self.assertRaises(ObsError) as caught:
            ObsPublicClient._normalize_program_type("Doktora")
        self.assertEqual(str(caught.exception), "Geçersiz program tipi: 'Doktora'. Geçerli değerler: LS, LU, LUİ, ÖL "
                                                "(veya 'Lisans', 'Lisansüstü', 'Önlisans').")

    def test_department_list_is_mapped_and_cached_per_program_type(self) -> None:
        client, _ = public()
        client._get_html = Mock(return_value=('[{"bransKoduId": 3, "dersBransKodu": "BLG", "x": 1}, {}]', BASE))  # type: ignore[method-assign]
        result = client.list_departments("Lisans")
        self.assertEqual(result, [{"brans_kodu_id": 3, "code": "BLG"}, {"brans_kodu_id": None, "code": None}])
        self.assertIs(client.list_departments("ls"), result)
        client._get_html.assert_called_once_with(
            "/public/DersProgram/SearchBransKoduByProgramSeviye", params={"programSeviyeTipiAnahtari": "LS"})
        client._get_html.return_value = ("<html>", BASE)
        with self.assertRaises(ObsError) as caught:
            client.list_departments("LU")
        self.assertEqual(str(caught.exception), "Bölüm listesi JSON parse edilemedi; OBS yanıtı değişmiş olabilir.")

    def test_active_semester(self) -> None:
        client, _ = public()
        client._get_html = Mock(return_value=('{"aktifDonem": "2040-2041 Güz"}', BASE))  # type: ignore[method-assign]
        self.assertEqual(client.get_active_semester("lisans"), "2040-2041 Güz")
        self.assertEqual(client.get_active_semester("LS"), "2040-2041 Güz")
        client._get_html.assert_called_once_with(
            "/public/DersProgram/GetAktifDonemByProgramSeviye", params={"programSeviyeTipiAnahtari": "LS"})
        client._get_html.return_value = ('{"aktifDonem": ""}', BASE)
        self.assertEqual(client.get_active_semester("LU"), "Bilinmeyen Dönem")
        client._get_html.return_value = ("<html>", BASE)
        with self.assertRaises(ObsError) as caught:
            client.get_active_semester("ÖL")
        self.assertEqual(str(caught.exception), "Dönem bilgisi JSON parse edilemedi.")

    def resolver(self, departments: list[dict[str, Any]] = DEPARTMENTS) -> ObsPublicClient:
        client, _ = public()
        client.list_departments = Mock(return_value=departments)  # type: ignore[method-assign]
        return client

    def test_department_by_exact_code_ignores_case_and_accents_and_is_remembered(self) -> None:
        client = self.resolver()
        self.assertEqual(client._resolve_department("LS", " blg "), 3)
        self.assertEqual(client._resolve_department("LS", "isl"), 12)
        client.list_departments.assert_called_with("LS")
        self.assertEqual(client._dept_cache, {3: DEPARTMENTS[0], 12: DEPARTMENTS[2]})

    def test_department_by_listed_numeric_id_is_not_remembered(self) -> None:
        client = self.resolver()
        self.assertEqual(client._resolve_department("LS", " 310 "), 310)
        self.assertEqual(client._dept_cache, {})

    def test_a_code_wins_over_an_id_of_the_same_text(self) -> None:
        client = self.resolver([{"brans_kodu_id": 101, "code": "ABC"}, {"brans_kodu_id": 5, "code": "101"}])
        self.assertEqual(client._resolve_department("LS", "101"), 5)

    def test_department_by_partial_code_takes_the_first_listed_either_way_round(self) -> None:
        client = self.resolver()
        self.assertEqual(client._resolve_department("LS", "b"), 3)
        self.assertEqual(client._resolve_department("LS", "bb"), 310)
        self.assertEqual(client._resolve_department("LS", "BLG 223E"), 3)
        self.assertEqual(client._dept_cache, {3: DEPARTMENTS[0], 310: DEPARTMENTS[1]})
        # Current behaviour, pinned as it is: empty text equals a missing code,
        # and otherwise it is part of every code.
        self.assertEqual(self.resolver()._resolve_department("LS", ""), 5)
        self.assertEqual(self.resolver(DEPARTMENTS[:3])._resolve_department("LS", " "), 3)

    def test_unknown_department_lists_the_first_twenty(self) -> None:
        for query in ("xyz", "999", "x y"):
            with self.subTest(query=query):
                with self.assertRaises(ObsError) as caught:
                    self.resolver()._resolve_department("LS", query)
                self.assertEqual(str(caught.exception), f"Bölüm kodu bulunamadı: {query!r}. Mevcut bölümler (ilk 20): "
                                                        "BLG (id=3), BBF (id=310), İŞL (id=12), None (id=5)")
        many = [{"brans_kodu_id": n, "code": f"Q{n:02d}"} for n in range(25)]
        with self.assertRaises(ObsError) as caught:
            self.resolver(many)._resolve_department("LS", "xyz")
        self.assertTrue(str(caught.exception).endswith("Q19 (id=19)"))


class CourseScheduleTests(unittest.TestCase):
    def client(self, semester: Any = "2040-2041 Güz") -> ObsPublicClient:
        client, _ = public()
        client.list_departments = Mock(return_value=DEPARTMENTS)  # type: ignore[method-assign]
        client._get_html = Mock(return_value=("<html></html>", BASE + "/s"))  # type: ignore[method-assign]
        client.get_active_semester = Mock(side_effect=[semester] if not isinstance(semester, Exception) else semester)  # type: ignore[method-assign]
        return client

    def read(self, client: ObsPublicClient, department: str, courses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        table = {"courses": courses or [], "count": len(courses or [])}
        with patch("ninova_mcp.parsing.extract_course_schedule_table", return_value=table) as extract:
            result = client.get_course_schedule("lisans", department)
        extract.assert_called_once_with("<html></html>", BASE + "/s", base_url=BASE)
        return result

    def test_schedule_is_fetched_stamped_and_cached_for_the_schedule_lifetime(self) -> None:
        client = self.client()
        client._cache.set = Mock(wraps=client._cache.set)  # type: ignore[method-assign]
        result = self.read(client, "blg")
        self.assertEqual({key: value for key, value in result.items() if key != "fetched_at"}, {
            "courses": [], "count": 0, "program_type": "LS", "department_code": "BLG", "department_id": 3,
            "semester": "2040-2041 Güz"})
        self.assertEqual(list(result)[-1], "fetched_at")
        self.assertTrue(result["fetched_at"].endswith("+00:00"))
        client._get_html.assert_called_once_with(
            "/public/DersProgram/DersProgramSearch", params={"ProgramSeviyeTipiAnahtari": "LS", "DersBransKoduId": "3"})
        client._cache.set.assert_called_once_with("schedule:LS:3", result, ttl_seconds=60.0)
        self.assertIs(client.get_course_schedule("LS", "BLG"), result)
        client._get_html.assert_called_once()

    def test_department_named_by_id_keeps_the_text_the_caller_gave(self) -> None:
        self.assertEqual(self.read(self.client(), " 310 ")["department_code"], " 310 ")

    def test_semester_failure_leaves_the_semester_empty(self) -> None:
        self.assertIsNone(self.read(self.client(ObsError("down")), "BLG")["semester"])

    def test_single_section_by_crn(self) -> None:
        client, _ = public()
        schedule = {"courses": [{"crn": "10001"}, {"crn": 10002, "code": "X"}], "count": 2, "semester": "S"}
        client.get_course_schedule = Mock(return_value=schedule)  # type: ignore[method-assign]
        result = client.get_course_schedule_by_crn("LS", "BLG", " 10002 ")
        self.assertEqual(result, {"courses": [{"crn": 10002, "code": "X"}], "count": 1, "semester": "S",
                                  "filtered_by_crn": " 10002 "})
        client.get_course_schedule.assert_called_once_with("LS", "BLG")
        with self.assertRaises(ObsError) as caught:
            client.get_course_schedule_by_crn("LS", "BLG", "9")
        self.assertEqual(str(caught.exception), "CRN 9 bulunamadı. Mevcut CRN'ler: 10001, 10002")
        client.get_course_schedule = Mock(return_value={"courses": [{"crn": n} for n in range(30)]})  # type: ignore[method-assign]
        with self.assertRaises(ObsError) as caught:
            client.get_course_schedule_by_crn("LS", "BLG", "x")
        self.assertTrue(str(caught.exception).endswith(", 18, 19"))
        client.get_course_schedule = Mock(return_value={})  # type: ignore[method-assign]
        with self.assertRaises(ObsError) as caught:
            client.get_course_schedule_by_crn("LS", "BLG", "x")
        self.assertEqual(str(caught.exception), "CRN x bulunamadı. Mevcut CRN'ler: ")


class DegreePlanTests(unittest.TestCase):
    FORM = """<select id="akademikBirimId"><option value="">Seçiniz</option>
              <option value="12">  Örnek   Fakültesi </option><option value="x">Geçersiz</option>
              <option value="7">Başka Fakülte</option></select>"""

    def test_faculties(self) -> None:
        client, _ = public()
        client._get_html = Mock(return_value=(self.FORM, BASE))  # type: ignore[method-assign]
        result = client.list_degree_faculties()
        self.assertEqual(result, [{"faculty_id": 12, "faculty_name": "Örnek Fakültesi"},
                                  {"faculty_id": 7, "faculty_name": "Başka Fakülte"}])
        self.assertIs(client.list_degree_faculties(), result)
        client._get_html.assert_called_once_with("/public/DersPlan")

    def test_faculty_form_problems(self) -> None:
        for html, message in (("<html></html>", "Ders planı akademik birim listesi bulunamadı."),
                              ('<select id="akademikBirimId"><option value="">x</option></select>',
                               "Ders planı akademik birim listesi boş döndü.")):
            client, _ = public()
            client._get_html = Mock(return_value=(html, BASE))  # type: ignore[method-assign]
            with self.assertRaises(ObsError) as caught:
                client.list_degree_faculties()
            self.assertEqual(str(caught.exception), message)

    def test_programs(self) -> None:
        client, _ = public()
        client._post_json = Mock(return_value=[{"programKodu": "BLG", "programAdi": "Örnek", "x": 1}, "junk", {}])  # type: ignore[method-assign]
        result = client.list_degree_programs(12, " LISANS ")
        self.assertEqual(result, [{"program_code": "BLG", "program_name": "Örnek"},
                                  {"program_code": None, "program_name": None}])
        self.assertIs(client.list_degree_programs(12), result)
        client._post_json.assert_called_once_with(
            "/public/DersPlan/GetAkademikProgramByBirimIdAndPlanTipi", data={"birimId": "12", "planTipiKodu": "lisans"})
        client._post_json.return_value = None
        self.assertEqual(client.list_degree_programs(13, "cap"), [])
        for method in (lambda: client.list_degree_programs(12, "Lisans!"),
                       lambda: client.list_degree_plans(faculty_id=12, program_code="BLG", plan_type="Lisans!")):
            with self.assertRaises(ObsError) as caught:
                method()
            self.assertEqual(str(caught.exception), "Geçersiz ders planı tipi: lisans!")

    def test_plan_versions(self) -> None:
        client, _ = public()
        client.list_degree_programs = Mock(return_value=[{"program_code": "blg"}, {"program_code": None}, {"program_code": "ABC"}])  # type: ignore[method-assign]
        client._get_html = Mock(return_value=("<html></html>", BASE + "/p"))  # type: ignore[method-assign]
        with patch("ninova_mcp.public_parsing.extract_degree_plan_list", return_value={"plans": []}) as extract:
            result = client.list_degree_plans(faculty_id=12, program_code=" blg ", plan_type=" Lisans ")
            self.assertIs(client.list_degree_plans(faculty_id=12, program_code="BLG"), result)
        self.assertEqual(result, {"plans": [], "faculty_id": 12, "program_code": "BLG", "plan_type": "lisans"})
        extract.assert_called_once_with("<html></html>", BASE + "/p")
        client._get_html.assert_called_once_with(
            "/public/DersPlan/DersPlanlariList", params={"PlanTipiKodu": "lisans", "programKodu": "BLG", "FakulteId": "12"})
        self.assertEqual(client.list_degree_programs.call_args_list, [call(12, "lisans"), call(12, "lisans")])
        with self.assertRaises(ObsError) as caught:
            client.list_degree_plans(faculty_id=12, program_code="xyz")
        self.assertEqual(str(caught.exception),
                         "Program kodu 'XYZ', fakülte 12 / lisans için bulunamadı. Geçerli kodlar: , ABC, BLG")

    def test_plan_detail(self) -> None:
        client, _ = public()
        client._get_html = Mock(return_value=("<html></html>", BASE + "/d"))  # type: ignore[method-assign]
        with patch("ninova_mcp.public_parsing.extract_degree_plan_detail", return_value={"semesters": []}) as extract:
            result = client.get_degree_plan(" 042 ")
            self.assertIs(client.get_degree_plan("042"), result)
        self.assertEqual(result, {"semesters": [], "plan_id": 42})
        extract.assert_called_once_with("<html></html>", BASE + "/d")
        client._get_html.assert_called_once_with("/public/DersPlan/DersPlanDetay/042")
        for bad in ("0", "-1", "x", "", "1.5", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ObsError) as caught:
                    client.get_degree_plan(bad)  # type: ignore[arg-type]
                self.assertEqual(str(caught.exception), "plan_id pozitif bir tamsayı olmalı.")


class AcademicCalendarTests(unittest.TestCase):
    EN, TR, QUERY = calendar_fixtures.EN_URL, calendar_fixtures.TR_URL, calendar_fixtures.YEAR_QUERY

    def routes(self) -> dict[str, str]:
        return {
            self.EN: calendar_fixtures.EN_HTML, self.TR: calendar_fixtures.TR_HTML,
            self.EN + self.QUERY.format(564): calendar_fixtures.previous_year(calendar_fixtures.EN_HTML),
            self.TR + self.QUERY.format(564): calendar_fixtures.previous_year(calendar_fixtures.TR_HTML),
        }

    def test_page_request_and_cache(self) -> None:
        client, session = calendar_fixtures.make_client(self.routes())
        page = client._calendar_page("en", "564")
        self.assertEqual(page["academic_year"], "2025-2026")
        self.assertIs(client._calendar_page("en", "564"), page)
        self.assertEqual(session.requested, [self.EN + self.QUERY.format(564)])
        self.assertIs(client._cache.get("academic_calendar:en:564"), page)
        current = client._calendar_page("tr", None)
        self.assertIs(client._cache.get("academic_calendar:tr:current"), current)

    def test_page_errors(self) -> None:
        client, session = calendar_fixtures.make_client({})
        for bad in ("x", "5a", "", "-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ObsError) as caught:
                    client._calendar_page("en", bad)
                self.assertEqual(str(caught.exception), "Academic calendar year id must be numeric")
        self.assertEqual(session.requested, [])
        with self.assertRaises(ObsError) as caught:
            client._calendar_page("en", None)
        self.assertEqual(str(caught.exception), "Academic calendar returned HTTP 404")
        with self.assertRaises(KeyError):
            client._calendar_page("de", None)

    def requested_for(self, date_from: str | None, date_to: str | None, routes: dict[str, str] | None = None) -> tuple[dict, list[str]]:
        client, session = calendar_fixtures.make_client(self.routes() if routes is None else routes)
        return client.get_academic_calendar(date_from, date_to), session.requested

    def test_which_pages_are_read_for_which_dates(self) -> None:
        current = [self.EN, self.TR]
        previous = [self.EN, self.EN + self.QUERY.format(564), self.TR + self.QUERY.format(564)]
        for dates, expected in (
            ((None, None), current),
            (("2026-11-01", "2026-12-01"), current),
            (("not a date", "2026-01-10"), current),
            (("2026-01-10", "also not"), current),
            (("2026-12-01", "2026-11-01"), current),
            (("2026-01-10", "2026-02-10"), previous),
            (("2026-01-10", "2026-11-01"), [*previous, self.TR]),
        ):
            with self.subTest(dates=dates):
                result, requested = self.requested_for(*dates)
                self.assertEqual(requested, expected)
                self.assertNotIn("coverage_warning", result)

    def test_a_year_page_that_answers_with_another_year_or_fails_is_reported(self) -> None:
        wrong = {**self.routes(), self.EN + self.QUERY.format(564): calendar_fixtures.EN_HTML}
        result, requested = self.requested_for("2026-01-10", "2026-02-10", wrong)
        self.assertEqual(requested, [self.EN, self.EN + self.QUERY.format(564)])
        self.assertIn("The site did not return the 2025-2026 calendar.", result["coverage_warning"])
        missing = {self.EN: calendar_fixtures.EN_HTML, self.TR: calendar_fixtures.TR_HTML}
        result, _ = self.requested_for("2026-01-10", "2026-02-10", missing)
        self.assertIn("The 2025-2026 calendar could not be read: Academic calendar returned HTTP 404",
                      result["coverage_warning"])
        result, _ = self.requested_for("2020-01-10", "2020-02-10")
        self.assertIn("The 2019-2020 academic year is not offered by the calendar page (older years are in its archive).",
                      result["coverage_warning"])


if __name__ == "__main__":
    unittest.main()
