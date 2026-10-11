"""Default tool results stay short, and what was left out is one argument away.

The payloads are synthetic (see ``synthetic_payloads``): a student with 8
terms, a degree plan of 45 rows and 6 registered courses, and a dashboard of
7 courses. Every client is a fake, so nothing here logs in or opens a socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

import synthetic_payloads as synthetic
from ninova_mcp import output_shape
from ninova_mcp.obs_schedule import weekly_schedule
from ninova_mcp.server import TOOLS, register_tools
from ninova_mcp.tool_metadata.common import INCLUDE_RAW_DESCRIPTIONS

# Upper bounds in bytes of the default JSON result, with roughly 40% headroom
# over the size measured when the bound was set (in the comment). Before the
# results were reduced the same calls returned: graduation 29385, GPA 5281,
# eligibility 2619, dashboard 12346, exam calendar 7213, registered courses
# 4551, schedule 6724, registration status 8873.
SIZE_BOUNDS = {
    "obs_get_graduation_remaining": 6000,  # 4226
    "obs_calculate_gpa": 3400,  # 2390
    "explain_course_eligibility": 1900,  # 1262
    "get_dashboard": 4500,  # 3063
    "get_personal_exam_calendar": 4500,  # 3161
    "obs_list_registered_courses": 4200,  # 2906
    "obs_get_schedule": 5000,  # 3448
    "obs_get_registration_status": 4200,  # 2818
}


def size(result: Any) -> int:
    return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))


class SyntheticAppCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = synthetic.build_app(self.temp_dir.name)


class DefaultResultSizeTests(SyntheticAppCase):
    def test_default_results_stay_under_their_bounds(self) -> None:
        calls = synthetic.measured_calls(self.app)
        for name, bound in SIZE_BOUNDS.items():
            with self.subTest(tool=name):
                measured = size(calls[name]())
                self.assertLessEqual(
                    measured, bound,
                    f"{name} returns {measured} bytes by default; the bound is {bound}. "
                    "Move the new data behind include_raw or raise the bound on purpose.",
                )

    def test_every_measured_call_runs(self) -> None:
        for name, call in synthetic.measured_calls(self.app).items():
            with self.subTest(tool=name):
                self.assertIsInstance(call(), dict)

    def test_default_results_carry_no_success_envelope(self) -> None:
        for name, call in synthetic.measured_calls(self.app).items():
            with self.subTest(tool=name):
                text = json.dumps(call(), ensure_ascii=False)
                self.assertNotIn("resultMessage", text)
                self.assertNotIn('"statusCode"', text)


class IncludeRawTests(SyntheticAppCase):
    def test_graduation_payload_is_opt_in(self) -> None:
        default = self.app.obs_get_graduation_remaining()
        self.assertNotIn("graduation", default)
        self.assertIn("graduation", default["raw_available"])
        self.assertEqual(default["summary"]["courses_total"], 45)
        self.assertEqual(len(default["summary"]["open_elective_slots"]), 1)
        # Static explanations are in the tool description, not in each result.
        self.assertNotIn("note", default["summary"])
        self.assertNotIn("credit_fields_note", default["summary"])
        full = self.app.obs_get_graduation_remaining(include_raw=True)
        self.assertEqual(full["graduation"], synthetic.graduation())
        self.assertEqual(full["academic_status"], synthetic.academic_status())
        self.assertNotIn("raw_available", full)
        self.assertGreater(size(full), 4 * size(default))

    def test_registration_status_payloads_are_opt_in(self) -> None:
        default = self.app.obs_get_registration_status()
        self.assertEqual(set(default), {"summary", "raw_available"})
        program = default["summary"]["per_program"][0]
        # The earlier terms are still answerable without the raw payload.
        self.assertEqual(program["term_history_total_count"], 8)
        self.assertEqual(program["term_history_count"], len(program["term_history"]))
        self.assertEqual(program["term_history"][0]["code"], "202620")
        self.assertEqual(program["term_history"][2], {
            "code": "202520", "name": "2024-2025 Bahar Dönemi", "gpa": 2.9, "cumulative_gpa": 2.75,
            "reported_term_credit": 18.0, "class_label": "3. Sınıf",
        })
        self.assertEqual(program["last_regular_term"], {"code": "202620", "same_as_latest_academic_term": True})
        for never_reported in ("max_credit", "blockers", "academic_standing", "class_level_source"):
            self.assertNotIn(never_reported, program)
        self.assertNotIn("name_en", program["latest_academic_term"])
        full = self.app.obs_get_registration_status(include_raw=True)
        self.assertEqual(full["kayit_durumu"], synthetic.registration_status())
        self.assertEqual(full["ders_kayit_durumu"], synthetic.lesson_registration_status())
        self.assertIn("max_credit_unavailable_reason", full["summary"]["per_program"][0])

    def test_registered_courses_state_the_term_once(self) -> None:
        default = self.app.obs_list_registered_courses()
        self.assertEqual(default["count"], 6)
        self.assertNotIn("akademikDonemAdiEN", default["semester"])
        row = default["kayitSinifResultList"][0]
        for repeated in ("akademikDonemAdi", "akademikDonemAdiEN", "akademikDonemId", "akademikDonemKodu",
                         "yerZamanBilgiEN", "dersTipiAdiEN"):
            self.assertNotIn(repeated, row)
        # Both titles are kept: the English one is not a copy of the Turkish one.
        self.assertEqual((row["dersAdiTR"], row["dersAdiEN"]), ("Sentetik Mimari", "Synthetic Architecture"))
        self.assertIn("yerZamanBilgiTR", row)
        self.assertEqual(row["ogretimUyesiAdSoyad"], "Dr. Öğr. Üyesi Sentetik Hoca 1")
        full = self.app.obs_list_registered_courses(include_raw=True)
        self.assertEqual(full["kayitSinifResultList"], synthetic.registered_courses()["kayitSinifResultList"])
        self.assertEqual(full["statusCode"], 0)

    def test_a_row_of_another_term_keeps_its_term(self) -> None:
        rows = [{"crn": 1, "akademikDonemKodu": "202620", "akademikDonemAdi": "A"},
                {"crn": 2, "akademikDonemKodu": "202610", "akademikDonemAdi": "B"}]
        semester = {"donemKodu": "202620", "akademikDonemAdi": "A"}
        self.assertEqual(output_shape.drop_repeated_term(rows, semester), [
            {"crn": 1}, {"crn": 2, "akademikDonemKodu": "202610", "akademikDonemAdi": "B"},
        ])

    def test_exam_calendar_names_matched_courses_by_reference(self) -> None:
        default = self.app.get_personal_exam_calendar()
        self.assertEqual(default["matched_courses"][0], {
            "sinifId": 900100, "crn": 31001, "bransKodu": "SNT", "dersKodu": "322E",
            "dersAdiTR": "Sentetik Mimari", "dersAdiEN": "Synthetic Architecture",
        })
        self.assertEqual(len(default["final_calendar"]["finalTakvimi"]), 6)
        self.assertNotIn("akademikDonemAdi", default["final_calendar"]["finalTakvimi"][0])
        self.assertTrue(default["untrusted_external_content"])
        full = self.app.get_personal_exam_calendar(include_raw=True)
        self.assertEqual(full["matched_courses"], synthetic.registered_courses()["kayitSinifResultList"])
        self.assertEqual(full["final_calendar"], synthetic.final_calendar())

    def test_eligibility_puts_the_verdict_first_and_the_history_behind_include_raw(self) -> None:
        default = self.app.explain_course_eligibility("SNT 499E", use_obs_history=True)
        self.assertEqual(list(default)[:4], ["course_code", "prerequisite_status", "eligible", "explanation"])
        self.assertIs(default["eligible"], False)
        self.assertEqual(default["completed_course_count"], 36)
        self.assertEqual(default["prerequisite_grades"], {"SNT 107E": "BA", "SNT 114E": "BB", "SNT 121E": "CB"})
        self.assertIn("SNT 121E (en az BB)", default["requirement"])
        for opt_in in ("completed_courses", "completed_grades", "requirement_tree", "minimum_grades",
                       "explanation_parts"):
            self.assertNotIn(opt_in, default)
            self.assertIn(opt_in, default["raw_available"])
        self.assertTrue(default["untrusted_external_content"])
        full = self.app.explain_course_eligibility("SNT 499E", use_obs_history=True, include_raw=True)
        self.assertEqual(len(full["completed_courses"]), 36)
        self.assertEqual(full["completed_grades"]["SNT 121E"], "CB")
        self.assertEqual(full["requirement_tree"], synthetic.PREREQUISITE_TREE)
        self.assertEqual(full["explanation"], " ".join(full["explanation_parts"]))
        self.assertNotIn("raw_available", full)
        # The verdict does not depend on the switch.
        for key in ("eligible", "explanation", "missing_courses", "credit_requirement_met"):
            self.assertEqual(full[key], default[key])

    def test_profile_redaction_does_not_depend_on_include_raw(self) -> None:
        profile = {**synthetic.ENVELOPE, "kisiselBilgiler": {"ad": "Sentetik", "tcKimlikNo": "10000000146",
                                                           "uyrukAdiTR": "T.C.", "uyrukAdiEN": "Turkey"}}
        with patch.object(self.app.obs, "get_profile", return_value=profile, create=True):
            default = self.app.obs_get_profile()
            full = self.app.obs_get_profile(include_raw=True)
        self.assertNotIn("10000000146", json.dumps(default))
        self.assertNotIn("10000000146", json.dumps(full))
        self.assertNotIn("statusCode", default)
        self.assertEqual(full["statusCode"], 0)


class DashboardTests(SyntheticAppCase):
    def test_links_and_headings_are_opt_in(self) -> None:
        default = self.app.get_dashboard()
        self.assertNotIn("links", default)
        self.assertEqual(default["page"], {
            "url": "https://ninova.itu.edu.tr/Kampus1", "title": "Ninova - Sentetik Kampüs",
        })
        self.assertEqual(len(default["courses"]), 7)
        self.assertEqual(len(default["recent_assignments"]), 5)
        full = self.app.get_dashboard(include_raw=True)
        self.assertEqual(default["link_count"], len(full["links"]))
        self.assertGreater(default["link_count"], 30)
        self.assertTrue(full["page"]["headings"])
        self.assertNotIn("raw_available", full)

    def test_compact_shortens_what_include_raw_added(self) -> None:
        both = self.app.get_dashboard(compact=True, include_raw=True)
        self.assertTrue(both["compact"])
        self.assertLess(len(both["links"]), both["link_count"])
        self.assertEqual(both["links_omitted"], both["link_count"] - 30)
        compact_only = self.app.get_dashboard(compact=True)
        self.assertTrue(compact_only["compact"])
        self.assertNotIn("links", compact_only)
        self.assertEqual(compact_only["link_count"], both["link_count"])

    def test_compact_default_from_the_environment_still_applies(self) -> None:
        with patch.dict(os.environ, {"NINOVA_COMPACT_DEFAULT": "1"}):
            result = self.app.get_dashboard()
        self.assertTrue(result["compact"])
        self.assertNotIn("links", result)

    def test_internal_course_discovery_is_not_reduced(self) -> None:
        self.assertEqual(self.app.list_courses(refresh=True)["count"], 7)


class ScheduleTests(SyntheticAppCase):
    def test_meetings_are_sorted_and_use_clock_times(self) -> None:
        result = self.app.obs_get_schedule()
        meetings = result["weekly_schedule"]
        self.assertEqual(result["meeting_count"], 7)
        self.assertEqual(
            [(item["day"], item["start"], item["course_code"]) for item in meetings],
            [("Pazartesi", "08:30", "SNT 322E"), ("Salı", "08:30", "ING 201"), ("Salı", "10:30", "SNT 336E"),
             ("Çarşamba", "08:30", "SNT 354E"), ("Perşembe", "08:30", "SNT 322E"),
             ("Perşembe", "13:30", "SNT 372E"), ("Cuma", "08:30", "MAT 281E")],
        )
        self.assertEqual(meetings[0], {
            "day": "Pazartesi", "start": "08:30", "end": "11:29", "course_code": "SNT 322E",
            "title": "Sentetik Mimari", "crn": "31001", "building": "EEB", "room": "A12", "campus": "Ayazağa",
        })
        self.assertNotIn("schedule", result)
        self.assertIn("finalTakvimi", result["final_calendar"])

    def test_raw_rows_are_opt_in(self) -> None:
        full = self.app.obs_get_schedule(include_raw=True)
        self.assertEqual(full["schedule"], synthetic.schedule())
        self.assertEqual(full["final_calendar"], synthetic.final_calendar())
        self.assertEqual(full["meeting_count"], 7)

    def test_an_unreadable_source_is_returned_instead_of_an_empty_week(self) -> None:
        odd = {**synthetic.ENVELOPE, "dersler": [{"crn": 31001, "saat": "bilinmiyor"}]}
        with patch.object(self.app.obs, "get_schedule", return_value=odd):
            result = self.app.obs_get_schedule()
        self.assertEqual(result["weekly_schedule"], [])
        self.assertEqual(result["weekly_schedule_status"], "source_shape_not_recognized")
        self.assertEqual(result["schedule"], {"dersler": [{"crn": 31001, "saat": "bilinmiyor"}]})

    def test_flat_rows_and_an_empty_term_are_recognized(self) -> None:
        flat = {"kayitSinifResultList": [
            {"crn": 5, "bransKodu": "SNT", "dersKodu": "101", "dersAdiTR": "Ders", "gunAdiEN": "Friday",
             "baslangicSaati": 600, "bitisSaati": 659, "mekanAdi": "D1"},
            {"crn": 6, "bransKodu": "SNT", "dersKodu": "102", "dersAdiTR": "Öteki", "gunAdiTR": "Salı",
             "baslangicSaati": "09:30", "bitisSaati": "10:20"},
        ]}
        meetings, recognized = weekly_schedule(flat)
        self.assertTrue(recognized)
        self.assertEqual([(item["day"], item["start"], item["end"]) for item in meetings],
                         [("Salı", "09:30", "10:20"), ("Friday", "10:00", "10:59")])
        self.assertEqual(meetings[1]["room"], "D1")
        self.assertEqual(weekly_schedule({"kayitSinifResultList": []}), ([], True))
        self.assertEqual(weekly_schedule("not a payload"), ([], False))


class TermGpaResultTests(SyntheticAppCase):
    def setUp(self) -> None:
        super().setUp()
        self.result = self.app.obs_calculate_gpa(semester=synthetic.GRADED_TERM)

    def test_a_shared_source_is_reported_once(self) -> None:
        self.assertEqual(self.result["credit_source"], "degree_plan")
        self.assertEqual(self.result["grade_source"], "degree_plan")
        for course in self.result["courses"]:
            self.assertNotIn("credit_source", course)
            self.assertNotIn("grade_source", course)
        for removed in ("credit_fallback_courses", "credit_fallback_note", "grade_fallback_courses",
                        "grade_fallback_note"):
            self.assertNotIn(removed, self.result)

    def test_only_the_exception_carries_its_own_source(self) -> None:
        rows = synthetic.registered_courses(synthetic.GRADED_TERM)
        rows["kayitSinifResultList"][0].update(kredi="4", harfNotu="AA")
        with patch.object(self.app.obs, "list_registered_courses", return_value=rows):
            result = self.app.obs_calculate_gpa(semester=synthetic.GRADED_TERM)
        self.assertEqual(result["credit_source"], "degree_plan")
        first, second = result["courses"][0], result["courses"][1]
        self.assertEqual((first["credit_source"], first["grade_source"]), ("registration", "registration"))
        self.assertNotIn("credit_source", second)

    def test_a_zero_credit_pass_fail_course_is_not_a_warning(self) -> None:
        self.assertTrue(self.result["calculation_complete"])
        self.assertNotIn("credits_unresolved", self.result)
        self.assertNotIn("credits_unresolved_note", self.result)
        self.assertEqual(self.result["excluded_courses"], [
            {"code": "ING 100", "grade": "BL", "credit": 0.0, "reason": "grade_excluded_from_gpa"},
        ])

    def test_a_course_that_needs_a_credit_and_has_none_is_still_named(self) -> None:
        rows = synthetic.registered_courses(synthetic.GRADED_TERM)
        rows["kayitSinifResultList"].append(
            {"bransKodu": "ZZZ", "dersKodu": "900", "dersAdiTR": "Kredisiz", "crn": 1, "kredi": None, "harfNotu": "AA"},
        )
        with patch.object(self.app.obs, "list_registered_courses", return_value=rows):
            result = self.app.obs_calculate_gpa(semester=synthetic.GRADED_TERM)
        self.assertEqual(result["credits_unresolved"], ["ZZZ 900"])
        self.assertIsNone(result["gpa"])
        # The average of the verified courses is shown only when it is not ``gpa``.
        self.assertEqual(result["known_courses_gpa"], 2.43)

    def test_failed_courses_are_named_by_code(self) -> None:
        self.assertEqual(self.result["ff_risk"], ["SNT 999E"])
        self.assertIn("SNT 999E", [course["code"] for course in self.result["courses"]])

    def test_static_texts_and_duplicate_numbers_are_gone(self) -> None:
        for removed in ("note", "comment", "scale", "average_scope", "calculated_term_gpa",
                        "covered_course_count", "known_courses_gpa", "projection_complete",
                        "applied_projection_count", "unused_projected_courses"):
            self.assertNotIn(removed, self.result)
        for course in self.result["courses"]:
            self.assertEqual(set(course), {"code", "name", "credit", "grade", "coefficient", "points", "crn"})
        self.assertEqual(self.result["gpa"], 2.43)
        self.assertEqual(self.result["official_term_gpa"], 2.9)
        self.assertEqual(self.result["preferred_term_gpa"], 2.9)
        self.assertEqual(set(self.result["official_reference"]), {"status", "program", "source", "finality"})
        self.assertNotIn("akademikDonemAdiEN", self.result["semester"])

    def test_a_projection_reports_its_own_fields(self) -> None:
        result = self.app.obs_calculate_gpa(
            semester=synthetic.GRADED_TERM, projected_grades={"SNT 999E": "BB", "SNT 000": "AA"},
        )
        self.assertTrue(result["is_projection"])
        self.assertEqual(result["applied_projection_count"], 1)
        self.assertFalse(result["projection_complete"])
        self.assertEqual(result["unmatched_projected_keys"], ["SNT 000"])
        self.assertEqual(result["ff_risk"], [])
        projected = next(course for course in result["courses"] if course["code"] == "SNT 999E")
        self.assertIs(projected["projected"], True)
        self.assertNotIn("projected", result["courses"][0])


class AcademicCalendarTests(unittest.TestCase):
    """The public calendar of the repository fixtures: 50 events between the two dates."""

    RANGE = {"date_from": "2026-09-01", "date_to": "2027-01-31"}

    def setUp(self) -> None:
        from test_academic_calendar_year import StubApp, make_client

        self.app = StubApp(make_client()[0])

    def test_an_event_carries_only_what_is_its_own(self) -> None:
        default = self.app.get_academic_calendar(**self.RANGE)
        full = self.app.get_academic_calendar(include_raw=True, **self.RANGE)
        self.assertEqual(default["event_count"], 50)
        self.assertEqual(default["event_count"], full["event_count"])
        self.assertEqual(default["timezone"], "Europe/Istanbul")
        for event, source in zip(default["events"], full["events"], strict=True):
            for shared in ("timezone", "source_url", "uid"):
                self.assertNotIn(shared, event)
            self.assertNotIn(None, event.values())
            self.assertNotIn([], event.values())
            self.assertEqual(source["timezone"], default["timezone"])
            self.assertEqual(source["source_url"], default["url"])
            # Everything that is left is unchanged.
            self.assertEqual(event, {key: source[key] for key in event})
            for needed in ("date", "description", "start_date", "end_date", "category", "scope"):
                self.assertIn(needed, event)
        self.assertNotIn("raw_available", full)
        self.assertIn("include_raw=true", default["raw_available"])

    def test_default_size_stays_under_its_bound(self) -> None:
        # 31206 bytes when the bound was set; 40221 before events were reduced.
        self.assertLessEqual(size(self.app.get_academic_calendar(**self.RANGE)), 37000)


class EnvelopeAndLanguageTests(unittest.TestCase):
    def test_success_envelope_is_dropped_at_every_level(self) -> None:
        payload = {**synthetic.ENVELOPE, "liste": [{"statusCode": 0, "resultMessage": "İşlem başarılı.", "x": 1}]}
        self.assertEqual(output_shape.drop_success_envelope(payload), {"liste": [{"x": 1}]})

    def test_failure_keeps_the_whole_envelope(self) -> None:
        for payload in (
            {"statusCode": 1, "resultCode": "ERRKayitZamani", "resultMessage": "Kayıt zamanı değil."},
            {"statusCode": "0", "resultCode": "SUCCESS", "resultMessage": "İşlem başarılı."},
            {"resultCode": "SUCCESS", "resultMessage": "İşlem başarılı."},
            {"statusCode": 0, "resultCode": "SUCCESS", "resultMessage": "Kayıt bulunamadı."},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(output_shape.drop_success_envelope(payload), payload)

    def test_a_result_code_that_says_more_than_success_is_kept(self) -> None:
        payload = {"statusCode": 0, "resultCode": "SCCSinifOgrenciYoklamaYok", "resultMessage": "İşlem başarılı."}
        self.assertEqual(output_shape.drop_success_envelope(payload), {"resultCode": "SCCSinifOgrenciYoklamaYok"})

    def test_the_input_is_not_modified(self) -> None:
        payload = {**synthetic.ENVELOPE, "satir": {"adTR": "a", "adEN": "b"}}
        before = json.dumps(payload, sort_keys=True)
        output_shape.reduce_obs(payload)
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_one_language_per_label(self) -> None:
        row = {
            "gunAdiTR": "Salı", "gunAdiEN": "Tuesday",
            "akademikDonemAdi": "2025-2026 Bahar", "akademikDonemAdiEN": "2025-2026 Spring",
            "dersAdiTR": "Fizik", "dersAdiEN": "Physics",
            "aciklama": None, "aciklamaEN": "Only in English",
            "bolumEN": "No Turkish twin", "GEN": "not a label",
        }
        self.assertEqual(output_shape.one_language(row), {
            "gunAdiTR": "Salı", "akademikDonemAdi": "2025-2026 Bahar",
            "dersAdiTR": "Fizik", "dersAdiEN": "Physics",
            "aciklama": None, "aciklamaEN": "Only in English",
            "bolumEN": "No Turkish twin", "GEN": "not a label",
        })

    def test_a_course_title_that_repeats_the_turkish_one_is_dropped(self) -> None:
        self.assertEqual(
            output_shape.one_language({"dersAdiTR": "Calculus I", "dersAdiEN": "calculus i"}),
            {"dersAdiTR": "Calculus I"},
        )


class IncludeRawSchemaTests(SyntheticAppCase):
    def test_every_include_raw_parameter_is_a_described_boolean(self) -> None:
        metadata = {tool["name"]: tool for tool in TOOLS}
        declared = {name for name, tool in metadata.items() if "include_raw" in tool["inputSchema"]["properties"]}
        self.assertEqual(declared, set(INCLUDE_RAW_DESCRIPTIONS))
        server = FastMCP("include-raw")
        register_tools(server, self.app, sorted(declared))
        published = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        for name in sorted(declared):
            with self.subTest(tool=name):
                listed = metadata[name]["inputSchema"]["properties"]["include_raw"]
                self.assertEqual(listed, {
                    "type": "boolean", "default": False, "description": INCLUDE_RAW_DESCRIPTIONS[name],
                })
                schema = published[name].inputSchema["properties"]["include_raw"]
                self.assertEqual(schema["type"], "boolean")
                self.assertIs(schema["default"], False)
                self.assertEqual(schema["description"], INCLUDE_RAW_DESCRIPTIONS[name])
                self.assertNotIn("include_raw", published[name].inputSchema.get("required", []))

    def test_other_parameters_keep_their_published_types(self) -> None:
        server = FastMCP("include-raw-neighbours")
        register_tools(server, self.app, ["obs_get_schedule", "get_dashboard", "explain_course_eligibility"])
        published = {tool.name: tool.inputSchema["properties"] for tool in asyncio.run(server.list_tools())}
        kinds = {option.get("type") for option in published["obs_get_schedule"]["semester"]["anyOf"]}
        self.assertLessEqual({"string", "integer"}, kinds)
        self.assertEqual(published["get_dashboard"]["compact"]["type"], "boolean")
        self.assertEqual(published["explain_course_eligibility"]["course_code"]["type"], "string")

    def test_a_registered_tool_result_keeps_its_provenance_marker(self) -> None:
        server = FastMCP("provenance")
        register_tools(server, self.app, ["obs_get_graduation_remaining"])
        _content, structured = asyncio.run(server.call_tool("obs_get_graduation_remaining", {}))
        result = structured["result"]
        self.assertIs(result["untrusted_external_content"], True)
        self.assertIn("content_notice", result)
        self.assertNotIn("graduation", result)


class SessionInfoTests(unittest.TestCase):
    def test_a_cookie_name_set_for_two_hosts_is_listed_once(self) -> None:
        from ninova_mcp.client import NinovaClient

        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, {
            "NINOVA_USERNAME": "dummy", "NINOVA_PASSWORD": "dummy", "NINOVA_STATE_DIR": state,
            "NINOVA_SESSION_PERSIST": "0",
        }):
            client = NinovaClient()
            client.session.cookies.set("ITUSSO", "a", domain="ninova.itu.edu.tr")
            client.session.cookies.set("ITUSSO", "b", domain="girisv3.itu.edu.tr")
            client.session.cookies.set("ASP.NET_SessionId", "c", domain="ninova.itu.edu.tr")
            self.assertEqual(client.session_info()["cookie_names"], ["ASP.NET_SessionId", "ITUSSO"])


if __name__ == "__main__":
    unittest.main()
