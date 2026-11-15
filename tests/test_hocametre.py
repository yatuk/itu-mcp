"""hocametre instructor ratings, with canned API answers and no network."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ninova_mcp import hocametre
from ninova_mcp.hocametre import (
    HocametreClient,
    HocametreError,
    HocametreToolsMixin,
    combine_ratings,
    same_person,
    search_term,
    split_instructors,
)

PEOPLE = {
    "main": {"id": "11111111-aaaa", "name": "Nizamettin Aydın", "slug": "nizamettin_aydinA", "email": "x@itu.edu.tr"},
    "dup": {"id": "22222222-bbbb", "name": "nizamettin aydın", "slug": "nizamettin_aydinB"},
    "other": {"id": "33333333-cccc", "name": "Nizamettin Bayyurt", "slug": "nizamettin_bayyurtC"},
    "surname": {"id": "44444444-dddd", "name": "Aydın", "slug": "aydinD"},
}
RATINGS = {
    "nizamettin_aydinA": [("GIVES_NOTES", 3.1, 19), ("IS_HELPFUL", 3.8, 20), ("TEACHING_SKILLS", 1.3, 19)],
    "nizamettin_aydinB": [("GIVES_NOTES", 4.0, 2), ("IS_HELPFUL", 4.5, 2), ("TEACHING_SKILLS", 2.0, 1)],
    "nizamettin_bayyurtC": [],
    "aydinD": [("TEACHING_SKILLS", 5.0, 3)],
}


class Resp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, session, method, url, *, validate_url, params=None, timeout=None):
        validate_url(url)
        self.calls.append((url, dict(params or {})))
        path = url.removeprefix(hocametre.API_URL)
        if path == "/instructors":
            term = params["filter"].removeprefix("name:like:")
            items = [p for p in PEOPLE.values() if term in hocametre.fold(p["name"])]
            return Resp({"totalItems": len(items), "items": items})
        if path.startswith("/instructors/ratings/by-slug/"):
            slug = path.rsplit("/", 1)[1]
            person = next(p for p in PEOPLE.values() if p["slug"] == slug)
            rows = [{"name": k, "averageRating": a, "count": c} for k, a, c in RATINGS[slug]]
            rows.append({"name": "ASSIGNS_HOMEWORK", "averageRating": 0, "count": 0})
            return Resp({"instructor": {**person, "faculty": {"name": "Bilgisayar ve Bilişim"}}, "ratings": rows})
        if path.startswith("/instructor-comments/instructor/"):
            return Resp({"totalItems": 1, "items": [{"comment": "  Sesi   kısık. ", "createdAt": "2025-02-19T10:00:00Z"}]})
        return Resp({"message": "nope"}, 404)


class FakeSchedule:
    def get_course_schedule(self, program_type, department):
        return {
            "semester": "2026-2027 Güz Dönemi",
            "courses": [
                {"crn": "100", "code": "BLG 223E", "name": "Data Structures", "instructor": "Nizamettin Aydın , Nizamettin Bayyurt",
                 "sessions": [], "capacity": 80, "enrolled": 70},
                {"crn": "101", "code": "BLG 223E", "name": "Data Structures", "instructor": "Nizamettin Aydın",
                 "sessions": [], "capacity": 80, "enrolled": 80},
                {"crn": "200", "code": "BLG 231E", "name": "Digital Circuits", "instructor": "Someone Else"},
            ],
        }


class HocametreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeApi()
        patcher = patch.object(hocametre, "request_with_safe_redirects", self.api)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = patch.object(hocametre.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.client = HocametreClient()

    def test_duplicate_profiles_are_merged_by_votes(self) -> None:
        result = self.client.lookup(["Prof. Dr. Nizamettin AYDIN"])
        entry = result["instructors"][0]
        self.assertEqual(entry["name"], "Nizamettin AYDIN")
        self.assertEqual([p["slug"] for p in entry["profiles"]], ["nizamettin_aydinA", "nizamettin_aydinB"])
        combined = {r["criterion"]: r for r in entry["combined"]["ratings"]}
        self.assertEqual(combined["GIVES_NOTES"]["count"], 21)
        self.assertAlmostEqual(combined["GIVES_NOTES"]["average"], round((3.1 * 19 + 4.0 * 2) / 21, 2))
        self.assertIsNone(combined["ASSIGNS_HOMEWORK"]["average"])
        self.assertEqual(entry["combined"]["profiles_merged"], 2)

    def test_other_people_and_bare_surnames_are_not_merged(self) -> None:
        slugs = [p["slug"] for p in self.client.lookup(["Nizamettin Aydın"])["instructors"][0]["profiles"]]
        self.assertNotIn("nizamettin_bayyurtC", slugs)
        self.assertNotIn("aydinD", slugs)

    def test_queries_are_sent_folded_to_ascii(self) -> None:
        self.client.search("Doç. Dr. Nizamettin Aydın")
        self.assertEqual(self.api.calls[0][1]["filter"], "name:like:nizamettin aydin")
        self.assertEqual(search_term("Ali: Çakmak, x"), "ali cakmak x")

    def test_unrated_criteria_are_no_data_and_email_is_dropped(self) -> None:
        profile = self.client.ratings("nizamettin_aydinA")
        rows = {r["criterion"]: r for r in profile["ratings"]}
        self.assertIsNone(rows["ASSIGNS_HOMEWORK"]["average"])
        self.assertIsNone(rows["TAKES_ATTENDANCE"]["average"])
        self.assertEqual(profile["faculty"], "Bilgisayar ve Bilişim")
        self.assertEqual(profile["max_votes"], 20)
        self.assertNotIn("email", profile)
        self.assertEqual(profile["profile_url"], "https://notkutusu.com/hocametre/nizamettin_aydinA")

    def test_comments_are_cleaned_and_sorted_newest_first(self) -> None:
        result = self.client.get_instructor("nizamettin_aydinA", comment_limit=3)
        self.assertEqual(result["comments"], [{"date": "2025-02-19", "comment": "Sesi kısık."}])
        params = self.api.calls[-1][1]
        self.assertEqual(params["sort"], "createdAt:desc")
        self.assertEqual(params["size"], 3)

    def test_repeated_reads_are_cached(self) -> None:
        self.client.ratings("nizamettin_aydinA")
        sent = len(self.api.calls)
        self.client.ratings("nizamettin_aydinA")
        self.assertEqual(len(self.api.calls), sent)

    def test_bad_input_sends_nothing(self) -> None:
        with self.assertRaises(HocametreError):
            self.client.search("Dr.")
        with self.assertRaises(HocametreError):
            self.client.ratings("../admin")
        with self.assertRaises(HocametreError):
            self.client.lookup([f"Name Number{i}" for i in range(11)])
        self.assertEqual(self.api.calls, [])

    def test_only_the_api_host_is_allowed(self) -> None:
        with self.assertRaises(HocametreError):
            hocametre._validate_url("https://evil.example.com/instructors")
        with self.assertRaises(HocametreError):
            hocametre._validate_url("http://mordor-api-production.notkutusu.com/instructors")

    def test_http_errors_are_reported(self) -> None:
        with patch.object(hocametre, "request_with_safe_redirects", lambda *a, **k: Resp({}, 500)):
            with self.assertRaisesRegex(HocametreError, "HTTP 500"):
                HocametreClient().ratings("nizamettin_aydinA")

    def test_course_sections_get_their_instructors_ratings(self) -> None:
        class App(HocametreToolsMixin):
            pass

        app = App()
        app.hocametre = self.client
        app.obs_public = FakeSchedule()
        result = app.hocametre_rate_course_sections(["blg223e"])
        course = result["courses"][0]
        self.assertEqual(course["code"], "BLG 223E")
        self.assertEqual(course["name"], "Data Structures")
        self.assertEqual([s["crn"] for s in course["sections"]], ["100", "101"])
        self.assertEqual(course["sections"][0]["instructors"], ["Nizamettin Aydın", "Nizamettin Bayyurt"])
        self.assertEqual(set(result["instructor_ratings"]), {"Nizamettin Aydın", "Nizamettin Bayyurt"})
        self.assertTrue(result["instructor_ratings"]["Nizamettin Aydın"]["found"])
        self.assertIsNone(result["instructor_ratings"]["Nizamettin Bayyurt"]["combined"])
        with self.assertRaises(HocametreError):
            app.hocametre_rate_course_sections(["not a code"])

    def test_helpers(self) -> None:
        self.assertEqual(split_instructors("Burak Berk Üstündağ , Gökhan İnce"), ["Burak Berk Üstündağ", "Gökhan İnce"])
        self.assertEqual(split_instructors("Prof. Dr. A B ve C D"), ["A B", "C D"])
        self.assertEqual(split_instructors("-"), [])
        self.assertTrue(same_person({"ayse", "tosun"}, {"ayse", "tosun", "kuhn"}))
        self.assertFalse(same_person({"ali", "cakmak"}, {"fatma", "pinar", "cakmak"}))
        self.assertFalse(same_person({"ali", "cakmak"}, {"cakmak"}))
        self.assertIsNone(combine_ratings([{"ratings": [{"criterion": "GIVES_NOTES", "average": None, "count": 0}]}]))

    def test_tools_stay_off_the_remote_transport(self) -> None:
        from ninova_mcp.tool_registry import REMOTE_TOOL_NAMES

        for name in hocametre.HOCAMETRE_TOOL_NAMES:
            self.assertNotIn(name, REMOTE_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
