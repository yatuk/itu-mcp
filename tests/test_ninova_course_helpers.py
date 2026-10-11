"""Course reference, assignment target and file directory helpers of the Ninova tools
(characterization, synthetic data, offline)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from ninova_mcp.client import NinovaError
from ninova_mcp.server import NinovaMcpApp

from recording import offline_app

BASE = "https://ninova.itu.edu.tr"
COURSE_URL = BASE + "/Sinif/7.1"
ROOT = COURSE_URL + "/SinifDosyalari"
NOT_UPLOAD_PAGE = "is not a Ninova assignment upload page (/Sinif/<id>.<id>/Odev/<id>/OdevGonder). Nothing was sent."


def course(code: str | None, title: str | None, section_id: int, **extra: Any) -> dict[str, Any]:
    return {"code": code, "title": title, "url": f"{BASE}/Sinif/7.{section_id}", **extra}


def assignment(title: str | None, number: int, **extra: Any) -> dict[str, Any]:
    return {"title": title, "url": f"{COURSE_URL}/Odev/{number}", **extra}


class AssignmentUploadTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.adapter = offline_app(self)
        self.course = course("ZZT 101", "Sentetik Giriş", 1)
        self.resolve_course = self.enterContext(patch.object(self.app, "_resolve_course", return_value=self.course))
        self.listing = self.enterContext(patch.object(self.app, "get_course_assignments", return_value={"assignments": []}))

    def resolve(self, **arguments: Any) -> dict[str, Any]:
        values = {"course": None, "assignment": None, "upload_url": None, **arguments}
        return self.app._resolve_assignment_upload_url(**values)

    def refused(self, message: str, **arguments: Any) -> None:
        with self.assertRaises(NinovaError) as caught:
            self.resolve(**arguments)
        self.assertEqual(str(caught.exception), message)

    def with_assignments(self, *items: dict[str, Any]) -> None:
        self.listing.return_value = {"assignments": list(items)}

    def test_upload_url_alone_decides_and_reads_nothing(self) -> None:
        upload = COURSE_URL + "/Odev/3/OdevGonder"
        for given, expected in (
            (upload, upload),
            ("/Sinif/7.1/Odev/3/OdevGonder", upload),
            (upload + "?a=1", upload + "?a=1"),
            ("/Sinif/7.1/Odev/3", upload),
            (COURSE_URL + "/Odev/3/", upload),
            (COURSE_URL + "/Odev/3?a=1#top", upload),
        ):
            with self.subTest(given=given):
                resolved = self.resolve(upload_url=given, course="ignored", assignment="ignored")
                self.assertEqual(list(resolved), ["upload_url", "course", "assignment"])
                self.assertEqual(resolved, {
                    "upload_url": expected, "course": None, "assignment": {"url": expected, "title": None},
                })
        self.resolve_course.assert_not_called()
        self.listing.assert_not_called()
        self.assertEqual(self.adapter.requests, [])

    def test_upload_url_that_is_not_an_upload_page_is_refused(self) -> None:
        for given in ("/Sinif/7.1/Odevler", "/Sinif/7.1/Odev/3/Baska", "/Kampus1"):
            with self.subTest(given=given):
                self.refused("upload_url " + NOT_UPLOAD_PAGE, upload_url=given)
        with self.assertRaisesRegex(NinovaError, "^Only https://ninova.itu.edu.tr URLs and Ninova paths are accepted"):
            self.resolve(upload_url="https://attacker.example/Sinif/7.1/Odev/3")
        self.assertEqual(self.adapter.requests, [])

    def test_course_and_assignment_are_both_needed_without_an_upload_url(self) -> None:
        message = "Provide upload_url, or both course and assignment (assignment title / URL / partial name)."
        for arguments in ({}, {"course": "ZZT 101"}, {"assignment": "Rapor"}, {"course": "", "assignment": "Rapor"},
                          {"course": 0, "assignment": "Rapor"}, {"course": "ZZT 101", "assignment": "", "upload_url": ""}):
            with self.subTest(arguments=arguments):
                self.refused(message, **arguments)
        self.resolve_course.assert_not_called()
        self.listing.assert_not_called()

    def test_assignments_are_read_by_the_course_url_with_details(self) -> None:
        self.with_assignments(assignment("Rapor", 1))
        self.resolve(course="ZZT 101", assignment="Rapor")
        self.resolve_course.assert_called_once_with("ZZT 101")
        self.listing.assert_called_once_with(COURSE_URL, limit=200, include_details=True)

    def test_title_url_and_parts_of_them_find_the_assignment(self) -> None:
        report = assignment("Dönem Raporu", 11, upload_url=COURSE_URL + "/Odev/11/OdevGonder")
        self.with_assignments(report, assignment("Sunum", 12))
        for reference in ("Dönem Raporu", "donem raporu", " DÖNEM  RAPORU ", "rapor", report["url"], "/odev/11", "Odev/11"):
            with self.subTest(reference=reference):
                resolved = self.resolve(course="ZZT 101", assignment=reference)
                self.assertEqual(list(resolved), ["upload_url", "course", "assignment"])
                self.assertEqual(resolved["upload_url"], COURSE_URL + "/Odev/11/OdevGonder")
                self.assertIs(resolved["course"], self.course)
                self.assertIs(resolved["assignment"], report)

    def test_unknown_assignment_lists_the_first_twelve_titles(self) -> None:
        self.with_assignments(assignment(None, 0), *(assignment(f"Ödev {number}", number) for number in range(1, 15)))
        titles = ", ".join(["?"] + [f"Ödev {number}" for number in range(1, 12)])
        self.refused(f"Assignment not found: Proje. Available: {titles}", course="ZZT 101", assignment="Proje")
        self.with_assignments()
        self.refused("Assignment not found: Proje. Available: ", course="ZZT 101", assignment="Proje")
        self.listing.return_value = {}
        self.refused("Assignment not found: Proje. Available: ", course="ZZT 101", assignment="Proje")

    def test_several_matches_are_an_error_unless_one_title_is_exact(self) -> None:
        first, second = assignment("Rapor", 1), assignment("Rapor 2", 2)
        self.with_assignments(first, second)
        self.assertIs(self.resolve(course="ZZT 101", assignment="rapor")["assignment"], first)
        self.refused("Ambiguous assignment 'Rap'. Matches: Rapor, Rapor 2", course="ZZT 101", assignment="Rap")
        self.with_assignments(first, assignment("Rapor", 3))
        self.refused("Ambiguous assignment 'Rapor'. Matches: Rapor, Rapor", course="ZZT 101", assignment="Rapor")

    def test_ambiguity_lists_at_most_eight_by_title_then_url(self) -> None:
        untitled = assignment(None, 50)
        self.with_assignments(untitled, *(assignment(f"Ödev 5{number}", 50 + number) for number in range(1, 10)))
        options = ", ".join([untitled["url"]] + [f"Ödev 5{number}" for number in range(1, 8)])
        self.refused(f"Ambiguous assignment 'odev/5'. Matches: {options}", course="ZZT 101", assignment="odev/5")
        self.with_assignments({"title": None, "url": None}, {"title": "", "url": ""})
        self.refused("Ambiguous assignment '  '. Matches: ?, ?", course="ZZT 101", assignment="  ")

    def test_reference_without_letters_matches_only_items_without_a_title_or_url(self) -> None:
        # Pinned as it is: a reference that normalizes to nothing picks an item whose title or URL is empty.
        blank = {"title": None, "url": COURSE_URL + "/Odev/9"}
        self.with_assignments(assignment("Rapor", 1), blank)
        self.assertIs(self.resolve(course="ZZT 101", assignment="   ")["assignment"], blank)
        self.with_assignments(assignment("Rapor", 1))
        self.refused("Assignment not found:    . Available: Rapor", course="ZZT 101", assignment="   ")

    def test_upload_link_comes_from_the_assignment_or_from_its_url(self) -> None:
        self.with_assignments(assignment("Rapor", 1, upload_url=COURSE_URL + "/Odev/77/OdevGonder"))
        self.assertEqual(self.resolve(course="ZZT 101", assignment="Rapor")["upload_url"], COURSE_URL + "/Odev/77/OdevGonder")
        for item in (assignment("Rapor", 1), {"title": "Rapor", "url": COURSE_URL + "/Odev/1/", "upload_url": ""}):
            with self.subTest(item=item):
                self.with_assignments(item)
                self.assertEqual(self.resolve(course="ZZT 101", assignment="Rapor")["upload_url"], COURSE_URL + "/Odev/1/OdevGonder")
        self.with_assignments({"title": "Rapor", "url": None})
        self.refused(
            "Assignment 'Rapor' has no upload URL (deadline may be closed or upload not enabled).",
            course="ZZT 101", assignment="Rapor",
        )

    def test_upload_link_read_from_the_page_must_be_an_upload_page(self) -> None:
        for link in (BASE + "/Kampus1", COURSE_URL + "/Odev/1"):
            with self.subTest(link=link):
                self.with_assignments(assignment("Rapor", 1, upload_url=link))
                self.refused("The assignment's upload link " + NOT_UPLOAD_PAGE, course="ZZT 101", assignment="Rapor")
        self.with_assignments({"title": "Rapor", "url": BASE + "/Kampus1"})
        self.refused("The assignment's upload link " + NOT_UPLOAD_PAGE, course="ZZT 101", assignment="Rapor")
        self.assertEqual(self.adapter.requests, [])

    def test_upload_page_check_returns_the_course_and_assignment_ids(self) -> None:
        self.assertEqual(self.app._require_upload_page_url("/Sinif/7.1/Odev/3/OdevGonder/", "x"), ("7.1", "3"))
        self.assertEqual(self.app._require_upload_page_url(COURSE_URL + "/Odev/3/OdevGonder?a=1", "x"), ("7.1", "3"))


class CourseReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.app._client = Mock(base_url=BASE)
        self.first = course("ZZT 101", "Sentetik Giriş", 1, crn="30101", section="CRN: 30101", context="ZZT 101 CRN: 30101")
        self.second = course("ZZT 202E", "Sentetik Devreler", 2, crn="30202")
        self.courses = [self.first, self.second]
        self.app.list_courses = Mock(side_effect=lambda: {"courses": self.courses})

    def refused(self, reference: Any, message: str) -> None:
        with self.assertRaises(NinovaError) as caught:
            self.app._resolve_course(reference)
        self.assertEqual(str(caught.exception), message)

    def test_reference_type_is_checked_before_the_course_list_is_read(self) -> None:
        for reference in (True, False, None, 1.5, ["ZZT 101"], {"code": "ZZT 101"}):
            with self.subTest(reference=reference):
                self.refused(reference, "course must be a course code, name, CRN or URL.")
        for reference in ("", "   ", "\t\n"):
            with self.subTest(reference=reference):
                self.refused(reference, "course must be a non-empty string.")
        self.app.list_courses.assert_not_called()

    def test_course_list_is_read_once_for_every_kind_of_reference(self) -> None:
        for reference in ("ZZT 101", 30202, self.first["url"], "/Sinif/9.9"):
            with self.subTest(reference=reference):
                self.app.list_courses.reset_mock()
                self.app._resolve_course(reference)
                self.app.list_courses.assert_called_once_with()

    def test_url_of_a_listed_course_returns_that_course(self) -> None:
        for reference in (
            self.second["url"], self.second["url"] + "/Odevler", "/Sinif/7.2", "/Sinif/7.2/Duyuru/5?x=1",
            "HTTPS://ninova.itu.edu.tr/Sinif/7.2", "x/Sinif/7.2",
        ):
            with self.subTest(reference=reference):
                self.assertIs(self.app._resolve_course(reference), self.second)

    def test_url_of_an_unlisted_course_is_returned_bare(self) -> None:
        for reference in ("/Sinif/9.9/Odevler", BASE + "/Sinif/9.9"):
            with self.subTest(reference=reference):
                resolved = self.app._resolve_course(reference)
                self.assertEqual(list(resolved), ["code", "title", "url", "context"])
                self.assertEqual(resolved, {"code": None, "title": None, "url": BASE + "/Sinif/9.9", "context": reference})

    def test_url_without_a_course_path_is_refused(self) -> None:
        message = "course_url must point to a Ninova course path like /Sinif/<id>.<id>."
        for reference in ("/Kampus1", BASE + "/Kampus1", "https://attacker.example/x", "/Sinif/abc", "ftp/Sinif/x"):
            with self.subTest(reference=reference):
                self.refused(reference, message)

    def test_exact_matches_are_returned_unmarked_and_tolerant_ones_say_how(self) -> None:
        self.assertIs(self.app._resolve_course("zzt101"), self.first)
        self.assertIs(self.app._resolve_course("sentetik devreler"), self.second)
        for reference, tier in (("ZZT 202", "code_without_suffix"), (30202, "crn"), ("devreler", "name_words"), ("vrel", "partial_text")):
            with self.subTest(reference=reference):
                resolved = self.app._resolve_course(reference)
                self.assertEqual(resolved, {**self.second, "matched_by": tier, "requested": str(reference)})
                self.assertEqual(list(resolved), [*self.second, "matched_by", "requested"])
                self.assertNotIn("matched_by", self.second)

    def test_crn_is_read_from_the_context_of_courses_stored_without_one(self) -> None:
        legacy = course("ZZT 303", "Sentetik Eski", 3, context="ZZT 303 crn 40404 Sentetik")
        self.courses = [self.first, legacy]
        self.assertEqual(self.app._resolve_course("40404")["url"], legacy["url"])
        self.assertEqual(self.app._resolve_course("CRN: 40404")["matched_by"], "crn")

    def test_unknown_course_message_names_up_to_twelve_courses(self) -> None:
        self.courses = [
            self.first, course("ZZT 404", None, 4), course(None, "Yalnız Ad", 5), course(None, None, 6),
            *(course(f"ZZT {number}", f"Ders {number}", number) for number in range(500, 510)),
        ]
        available = ", ".join([
            "ZZT 101 (Sentetik Giriş)", "ZZT 404", "Yalnız Ad", BASE + "/Sinif/7.6",
            *(f"ZZT {number} (Ders {number})" for number in range(500, 508)),
        ])
        self.refused("YYY 999", f"Course not found: YYY 999. Available courses: {available}.")
        self.courses = []
        self.refused("YYY 999", "Course not found: YYY 999. Course list is empty.")
        self.refused(12345, "Course not found: 12345. Course list is empty.")

    def test_ambiguous_reference_describes_each_candidate(self) -> None:
        labelled = course("ZZT 500", "Sentetik Staj", 4, section="2031 Exemption", context="ZZT 500 2031 Exemption")
        with_crn = course("ZZT 500", "Sentetik Staj", 5, crn="30505", section="CRN: 30505")
        both = course("ZZT 500", None, 6, crn="30606", section="B Grubu")
        legacy = course(None, "ZZT 500", 7, context="ZZT 500 CRN:30707")
        self.courses = [labelled, with_crn, both, legacy]
        self.refused("ZZT 500", (
            "Ambiguous course reference: ZZT 500. Matches: "
            f"ZZT 500 (Sentetik Staj; section 2031 Exemption; {BASE}/Sinif/7.4); "
            f"ZZT 500 (Sentetik Staj; CRN 30505; {BASE}/Sinif/7.5); "
            f"ZZT 500 (?; section B Grubu; CRN 30606; {BASE}/Sinif/7.6); "
            f"? (ZZT 500; CRN 30707; {BASE}/Sinif/7.7). "
            "Pass the URL of the one you mean."
        ))

    def test_ambiguity_lists_at_most_eight_candidates(self) -> None:
        self.courses = [course("ZZT 500", "Sentetik Staj", number) for number in range(1, 11)]
        with self.assertRaises(NinovaError) as caught:
            self.app._resolve_course("ZZT 500")
        message = str(caught.exception)
        self.assertEqual(message.count("ZZT 500 (Sentetik Staj; "), 8)
        self.assertIn("/Sinif/7.8). Pass the URL", message)


def file_table(*rows: tuple[str, str, str]) -> str:
    return "<table><tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>" + "".join(
        f'<tr><td><img src="/img/{kind}.png"><a href="{url}">{name}</a></td><td>1 KB</td><td>1 Eylül 2026</td></tr>'
        for name, url, kind in rows
    ) + "</table>"


class FileClient:
    base_url = BASE

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get_html(self, url: str) -> tuple[str, Any]:
        self.requested.append(url)
        value = self.pages.get(url, "<html><body>Unrecognized synthetic page</body></html>")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return value[0], SimpleNamespace(url=value[1])
        return value, SimpleNamespace(url=url)


class FileDirectoryWalkTests(unittest.TestCase):
    FOLDER = ROOT + "?g=1"
    INNER = ROOT + "?g=2"

    def setUp(self) -> None:
        self.pages: dict[str, Any] = {
            ROOT: file_table(("Syllabus.pdf", ROOT + "?f=1", "pdf"), ("Notlar", self.FOLDER, "folder")),
            self.FOLDER: file_table(("Hafta1.pdf", ROOT + "?f=2", "pdf"), ("Ek", self.INNER, "folder")),
            self.INNER: file_table(("Ek1.pdf", ROOT + "?f=3", "pdf")),
        }
        self.app = NinovaMcpApp.__new__(NinovaMcpApp)
        self.client = FileClient(self.pages)
        self.app._client = self.client

    def walk(self, root: str = ROOT, *, recursive: bool = True, max_depth: int = 3) -> dict[str, Any]:
        return self.app._walk_file_directory(root, recursive=recursive, max_depth=max_depth)

    def page(self, url: str, path: str, count: int, coverage: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"url": url, "current_path": path, "entry_count": count, "coverage": coverage or {"status": "complete"}}

    def test_whole_tree_is_walked_breadth_first(self) -> None:
        result = self.walk("/Sinif/7.1/SinifDosyalari")
        self.assertEqual(list(result), [
            "root_url", "recursive", "max_depth", "pages_visited", "pages", "entry_count", "entries", "coverage", "errors",
        ])
        self.assertEqual(self.client.requested, [ROOT, self.FOLDER, self.INNER])
        self.assertEqual(result["root_url"], ROOT)
        self.assertEqual((result["recursive"], result["max_depth"]), (True, 3))
        self.assertEqual(result["pages_visited"], 3)
        self.assertEqual(result["pages"], [
            self.page(ROOT, "/", 2), self.page(self.FOLDER, "/Notlar", 2), self.page(self.INNER, "/Notlar/Ek", 1),
        ])
        self.assertEqual(result["entry_count"], 5)
        self.assertEqual(
            [(entry["path"], entry["entry_type"]) for entry in result["entries"]],
            [("/Syllabus.pdf", "file"), ("/Notlar", "folder"), ("/Notlar/Hafta1.pdf", "file"),
             ("/Notlar/Ek", "folder"), ("/Notlar/Ek/Ek1.pdf", "file")],
        )
        self.assertEqual(result["coverage"], {
            "status": "complete", "max_depth": 3, "recursive": True, "unvisited_folder_count": 0,
        })
        self.assertEqual(result["errors"], [])

    def test_without_recursion_folders_are_listed_but_reported_unvisited(self) -> None:
        result = self.walk(recursive=False)
        self.assertEqual(self.client.requested, [ROOT])
        self.assertEqual([entry["path"] for entry in result["entries"]], ["/Syllabus.pdf", "/Notlar"])
        self.assertEqual(result["errors"], [{"path": ROOT, "error": "file_depth_or_recursion_limit"}])
        self.assertEqual(result["coverage"], {
            "status": "partial", "max_depth": 3, "recursive": False, "unvisited_folder_count": 1,
            "reason": "incomplete_file_inventory",
        })

    def test_depth_limit_stops_the_walk_and_is_reported(self) -> None:
        result = self.walk(max_depth=1)
        self.assertEqual(self.client.requested, [ROOT, self.FOLDER])
        self.assertEqual(result["pages_visited"], 2)
        self.assertEqual(result["entry_count"], 4)
        self.assertEqual(result["errors"], [{"path": ROOT, "error": "file_depth_or_recursion_limit"}])
        self.assertEqual(result["coverage"]["unvisited_folder_count"], 1)
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertEqual(self.walk(max_depth=0)["coverage"]["unvisited_folder_count"], 1)
        self.assertEqual(self.walk(max_depth=-1)["pages_visited"], 1)

    def test_folder_that_was_already_read_is_neither_read_again_nor_unvisited(self) -> None:
        self.pages[self.FOLDER] = file_table(("Geri", ROOT + "#ust", "folder"), ("Kendisi", self.FOLDER, "folder"))
        result = self.walk()
        self.assertEqual(self.client.requested, [ROOT, self.FOLDER])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["coverage"]["status"], "complete")
        self.assertEqual(result["entry_count"], 4)
        limited = self.walk(max_depth=1)
        self.assertEqual(limited["errors"], [])
        self.assertEqual(limited["coverage"]["unvisited_folder_count"], 0)

    def test_same_folder_linked_twice_is_read_once(self) -> None:
        self.pages[ROOT] = file_table(("Notlar", self.FOLDER, "folder"), ("Notlar yine", self.FOLDER + "#x", "folder"))
        self.pages[self.FOLDER] = file_table(("Hafta1.pdf", ROOT + "?f=2", "pdf"))
        result = self.walk()
        self.assertEqual(self.client.requested, [ROOT, self.FOLDER])
        self.assertEqual(result["entry_count"], 3)
        self.assertEqual(result["errors"], [])

    def test_folder_that_cannot_be_read_is_an_error_and_the_rest_continues(self) -> None:
        self.pages[self.FOLDER] = TimeoutError("Synthetic folder timeout")
        result = self.walk()
        self.assertEqual(self.client.requested, [ROOT, self.FOLDER])
        self.assertEqual(result["pages"], [self.page(ROOT, "/", 2)])
        self.assertEqual(result["errors"], [{"path": self.FOLDER, "error": "Synthetic folder timeout"}])
        self.assertEqual(result["coverage"], {
            "status": "partial", "max_depth": 3, "recursive": True, "unvisited_folder_count": 0,
            "reason": "incomplete_file_inventory",
        })

    def test_root_that_cannot_be_read_is_a_failed_inventory(self) -> None:
        self.pages[ROOT] = NinovaError("Synthetic refusal")
        result = self.walk()
        self.assertEqual(result["pages"], [])
        self.assertEqual(result["entries"], [])
        self.assertEqual((result["pages_visited"], result["entry_count"]), (0, 0))
        self.assertEqual(result["errors"], [{"path": ROOT, "error": "Synthetic refusal"}])
        self.assertEqual(result["coverage"], {
            "status": "failed", "max_depth": 3, "recursive": True, "unvisited_folder_count": 0,
            "reason": "incomplete_file_inventory",
        })

    def test_parser_failure_is_recorded_like_a_failed_read(self) -> None:
        with patch("ninova_mcp.app_ninova.extract_file_directory", side_effect=ValueError("Synthetic markup")):
            result = self.walk()
        self.assertEqual(result["errors"], [{"path": ROOT, "error": "Synthetic markup"}])
        self.assertEqual(result["coverage"]["status"], "failed")
        with patch("ninova_mcp.app_ninova.scope_coverage", side_effect=ValueError("Synthetic coverage")):
            result = self.walk()
        self.assertEqual(result["errors"], [{"path": ROOT, "error": "Synthetic coverage"}])
        self.assertEqual(result["pages"], [])

    def test_page_that_is_not_fully_understood_is_kept_and_reported(self) -> None:
        self.pages[self.FOLDER] = "<html><body>Unrecognized synthetic page</body></html>"
        result = self.walk()
        unknown = {"status": "unknown", "reason": "file_table_not_recognized"}
        self.assertEqual(result["pages"], [self.page(ROOT, "/", 2), self.page(self.FOLDER, "/Notlar", 0, unknown)])
        self.assertEqual(result["errors"], [{"path": self.FOLDER, "error": "file_table_not_recognized"}])
        self.assertEqual(result["coverage"]["status"], "partial")

    def test_redirected_page_is_reported_under_the_url_that_was_asked_for(self) -> None:
        self.pages[self.FOLDER] = (file_table(("Baska", self.INNER, "folder")), ROOT)
        result = self.walk()
        failed = {"status": "failed", "reason": "unexpected_resource_query"}
        self.assertEqual(result["pages"][1], self.page(ROOT, "/Notlar", 1, failed))
        self.assertEqual(result["errors"], [{"path": self.FOLDER, "error": "unexpected_resource_query"}])
        # The folder it listed is still followed.
        self.assertEqual(self.client.requested, [ROOT, self.FOLDER, self.INNER])

    def test_every_page_is_judged_as_class_files_against_the_requested_url(self) -> None:
        with patch("ninova_mcp.app_ninova.scope_coverage", return_value={"status": "complete"}) as judge:
            self.walk(COURSE_URL + "/DersDosyalari")
        self.assertEqual(judge.call_count, 1)
        html, page_url, expected_url, scope, listing = judge.call_args.args
        self.assertEqual((page_url, expected_url, scope), (COURSE_URL + "/DersDosyalari",) * 2 + ("class_files",))
        self.assertEqual(listing["entries"], [])

    def test_tools_label_the_walk_with_the_course_and_scope(self) -> None:
        listed = course("ZZT 101", "Sentetik Giriş", 1)
        self.app.list_courses = Mock(return_value={"courses": [listed]})
        self.app._out = lambda payload, compact=False: payload
        self.pages[COURSE_URL + "/DersDosyalari"] = file_table()
        class_files = self.app.get_course_class_files("ZZT 101", recursive=False)
        self.assertEqual((class_files["course"], class_files["scope"]), (listed, "class_files"))
        self.assertEqual(class_files["root_url"], ROOT)
        lesson_files = self.app.get_course_lesson_files("ZZT 101", max_depth=1)
        self.assertEqual((lesson_files["course"], lesson_files["scope"]), (listed, "lesson_files"))
        self.assertEqual((lesson_files["root_url"], lesson_files["max_depth"]), (COURSE_URL + "/DersDosyalari", 1))


if __name__ == "__main__":
    unittest.main()
