"""``export_table`` and the CSV writer behind it.

No request leaves the process: every ``ObsClient`` method the tool uses is
patched and the tracking state is a file in a temporary directory. All record
values are invented.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ninova_mcp import table_export
from ninova_mcp.client import NinovaError
from ninova_mcp.server import (
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    TOOLS,
    NinovaMcpApp,
)
from ninova_mcp.table_export import BOM, cell_text, render_csv
from ninova_mcp.toolsets import TOOL_TO_TOOLSET

NAME = "export_table"
SEMESTER = {"akademikDonemId": 77, "donemKodu": "202510", "akademikDonemAdi": "2024-2025 Güz"}
GRADUATION = {"mezuniyetimeNeKaldiBilgi": {
    "gpa": 2.0,
    "checkMetMezuniyetList": [
        {"isMet": True, "bransKodu": "ABC 101E", "dersAdi": "Aygıtlara Giriş", "harfNotu": "CC",
         "donem": "202510", "kredisi": "4", "kredisiDec": 4, "sayilanKredi": 4},
        {"isMet": True, "bransKodu": "ABC 102", "dersAdi": "Aygıt Lab.", "harfNotu": "BL",
         "donem": "202510", "kredisi": "0", "kredisiDec": 0, "sayilanKredi": 0},
        {"isMet": True, "bransKodu": "ABC 101E", "dersAdi": "Aygıtlara Giriş", "harfNotu": "FF",
         "donem": "202410", "kredisi": "4", "kredisiDec": 4, "sayilanKredi": 4},
    ],
    "unusedSinifOgrenciList": [],
}}
STATUS = {"statusCode": 0, "kayitDurumuList": [{"kayitDurumuDonemList": [
    {"akademikDonemKodu": "202410", "donemlikNotOrtalamasi": 0.0, "genelNotOrtalamasi": 0.0, "verilenKredi": 0},
    {"akademikDonemKodu": "202510", "donemlikNotOrtalamasi": 2.0, "genelNotOrtalamasi": 2.0, "verilenKredi": 4},
]}]}
REGISTERED = {"statusCode": 0, "kayitSinifResultList": [
    {"bransKodu": "ABC", "dersKodu": "101E", "crn": "31001", "dersAdiTR": "Aygıtlara Giriş", "kredi": 4,
     "harfNotu": None},
    {"bransKodu": "ABC", "dersKodu": "300", "crn": "31003", "dersAdiTR": "=Şüpheli; Başlık", "kredi": 3.5,
     "harfNotu": None},
]}
SCHEDULE = {"kayitSinifResultList": [
    {"bransKodu": "ABC", "dersKodu": "300", "crn": 31003, "dersAdiTR": "Çizge Kuramı", "sinifYerZaman": [
        {"gunAdiTR": "Salı", "baslangicSaati": 510, "bitisSaati": 620, "binaAdi": "EEB",
         "mekanKapiTabelasi": "5102"},
    ]},
    {"bransKodu": "ABC", "dersKodu": "101E", "crn": 31001, "dersAdiTR": "Aygıtlara Giriş", "sinifYerZaman": [
        {"gunAdiTR": "Pazartesi", "baslangicSaati": 810, "bitisSaati": 920, "binaAdi": "MED",
         "mekanKapiTabelasi": "A21"},
    ]},
]}


def parse(text: str, delimiter: str = ";") -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.removeprefix(BOM), newline=""), delimiter=delimiter))


class CsvWriterTests(unittest.TestCase):
    def test_bom_delimiter_and_line_endings(self) -> None:
        text, written = render_csv(["A", "B"], [("x", 1), ("y", 2)])
        self.assertEqual(text, "﻿A;B\r\nx;1\r\ny;2\r\n")
        self.assertEqual(written, 2)
        self.assertEqual(text.encode("utf-8")[:3], b"\xef\xbb\xbf")
        self.assertEqual(text.encode("utf-8").decode("utf-8-sig"), text.removeprefix(BOM))

    def test_decimal_mark_follows_the_delimiter(self) -> None:
        rows = [(3.5, 4.0, -1.25, 7, None, True, False, float("nan"))]
        columns = list("abcdefgh")
        self.assertEqual(parse(render_csv(columns, rows)[0])[1],
                         ["3,5", "4", "-1,25", "7", "", "Evet", "Hayır", ""])
        self.assertEqual(parse(render_csv(columns, rows, delimiter=",")[0], ",")[1],
                         ["3.5", "4", "-1.25", "7", "", "Evet", "Hayır", ""])

    def test_only_two_delimiters_are_accepted(self) -> None:
        for bad in ("\t", "|", "", ";;"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                render_csv(["A"], [], delimiter=bad)

    def test_cells_with_delimiter_quote_and_newline_are_quoted(self) -> None:
        cells = ("a;b", 'say "x"', "line1\nline2", "c,d")
        text, _ = render_csv(["1", "2", "3", "4"], [cells])
        self.assertIn('"a;b";"say ""x""";"line1\nline2";c,d\r\n', text)
        self.assertEqual(parse(text)[1], list(cells))
        comma, _ = render_csv(["1", "2", "3", "4"], [cells], delimiter=",")
        self.assertIn('a;b,"say ""x""","line1\nline2","c,d"\r\n', comma)

    def test_formula_triggers_are_prefixed(self) -> None:
        for trigger in ("=", "+", "-", "@", "\t", "\r"):
            with self.subTest(trigger=repr(trigger)):
                payload = trigger + "HYPERLINK(1)"
                self.assertEqual(cell_text(payload), "'" + payload)
                self.assertEqual(parse(render_csv(["A"], [(payload,)])[0])[1], ["'" + payload])
        # A header is a cell too.
        self.assertEqual(parse(render_csv(["=A"], [])[0])[0], ["'=A"])

    def test_text_is_never_written_as_a_negative_number(self) -> None:
        self.assertEqual(cell_text("-3"), "'-3")
        self.assertEqual(cell_text(-3), "-3")
        self.assertEqual(cell_text(-3.5), "-3,5")
        self.assertEqual(cell_text("BA+"), "BA+")  # a trigger that is not first is left alone

    def test_turkish_characters_round_trip(self) -> None:
        cells = ["İstanbul Çığ Şöğüt", "ıİğĞüÜşŞöÖçÇ"]
        text, _ = render_csv(["Öğe", "Değer"], [cells])
        decoded = text.encode("utf-8").decode("utf-8-sig")
        self.assertEqual(list(csv.reader(io.StringIO(decoded, newline=""), delimiter=";")),
                         [["Öğe", "Değer"], cells])

    def test_empty_table_is_the_header_line(self) -> None:
        self.assertEqual(render_csv(["Ders Kodu", "Kredi"], []), ("﻿Ders Kodu;Kredi\r\n", 0))

    def test_size_limit_stops_at_a_row_boundary(self) -> None:
        rows = [("x" * 10,)] * 100
        text, written = render_csv(["A"], rows, max_bytes=200)
        self.assertLess(written, 100)
        self.assertLessEqual(len(text.encode("utf-8")), 200)
        self.assertTrue(text.endswith("\r\n"))
        self.assertEqual(len(parse(text)), written + 1)

    def test_row_width_must_match(self) -> None:
        with self.assertRaises(ValueError):
            render_csv(["A", "B"], [("only",)])


class ExportTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = patch.dict(os.environ, {
            "NINOVA_USERNAME": "dummy", "NINOVA_PASSWORD": "dummy", "NINOVA_STATE_DIR": self.temp_dir.name,
        })
        env.start()
        self.addCleanup(env.stop)
        self.app = NinovaMcpApp()
        self.mocks: dict[str, Any] = {}
        for method, value in (
            ("resolve_semester", SEMESTER),
            ("list_registered_courses", REGISTERED),
            ("default_program_id", 5),
            ("get_graduation_remaining", GRADUATION),
            ("get_registration_status", STATUS),
            ("get_schedule", SCHEDULE),
        ):
            patcher = patch.object(self.app.obs, method, return_value=value)
            self.mocks[method] = patcher.start()
            self.addCleanup(patcher.stop)

    def write_tracking_state(self, synced_at: datetime) -> None:
        stamp = synced_at.isoformat()
        soon = (datetime.now(tz=UTC) + timedelta(days=3)).replace(microsecond=0).isoformat()
        state = {"version": 2, "last_sync_at": stamp, "updates": [], "courses": {"/Sinif/1": {
            "course": {"code": "ABC 300", "title": "Çizge Kuramı", "url": "/Sinif/1"},
            "synced_at": stamp,
            "snapshot": {
                "coverage": {"assignments": {"status": "complete"}},
                "overview": {"assignments": [
                    {"title": "@Ödev 2", "submission_end": "sonra", "submission_end_iso": soon,
                     "requested_file_count": 1, "uploaded_file_count": 0},
                    {"title": "Ödev 1", "submission_end": "01 Mart 2024 23:30",
                     "submission_end_iso": "2024-03-01T23:30:00+03:00",
                     "requested_file_count": 2, "uploaded_file_count": 2},
                    {"title": "Tarihsiz", "submission_end": None},
                ]},
            },
        }}}
        self.app.tracking_state_path.write_text(json.dumps(state), encoding="utf-8")

    # -------------------------------------------------------------- datasets

    def test_term_grades(self) -> None:
        result = self.app.export_table("term_grades", semester=202510)
        self.mocks["resolve_semester"].assert_called_once_with("202510")
        self.assertEqual(result["columns"], ["Ders Kodu", "Ders Adı", "Kredi", "Harf Notu"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["mime_type"], "text/csv")
        self.assertEqual(result["suggested_filename"], "itu-donem-notlari-202510.csv")
        self.assertEqual(result["semester"], {"id": 77, "code": "202510", "name": "2024-2025 Güz"})
        self.assertEqual((result["term_gpa"], result["term_gpa_source"]), (2.0, "official_obs"))
        self.assertEqual(result["graded_course_count"], 1)
        rows = parse(result["csv"])
        self.assertEqual(rows[0], result["columns"])
        # The grade comes from the graduation plan of this term, not from the earlier FF.
        self.assertEqual(rows[1], ["ABC 101E", "Aygıtlara Giriş", "4", "CC"])
        # An untrusted title cannot become a formula, and its ";" stays inside one cell.
        self.assertEqual(rows[2], ["ABC 300", "'=Şüpheli; Başlık", "3,5", ""])
        self.assertTrue(result["csv"].startswith(BOM))
        self.assertNotIn("truncated", result)

    def test_all_grades_marks_the_superseded_attempt(self) -> None:
        result = self.app.export_table("all_grades", semester="202510")
        self.mocks["resolve_semester"].assert_not_called()
        self.assertIs(result["semester_ignored"], True)
        self.assertNotIn("semester", result)
        self.assertEqual(result["columns"], ["Dönem", "Ders Kodu", "Ders Adı", "Kredi", "Harf Notu", "Durum"])
        self.assertEqual(result["suggested_filename"], "itu-tum-notlar.csv")
        self.assertEqual(parse(result["csv"])[1:], [
            ["202410", "ABC 101E", "Aygıtlara Giriş", "4", "FF", "Tekrar edildi"],
            ["202510", "ABC 101E", "Aygıtlara Giriş", "4", "CC", "Sayılıyor"],
            ["202510", "ABC 102", "Aygıt Lab.", "0", "BL", "Ortalamaya girmez"],
        ])
        self.assertEqual((result["row_count"], result["counted_count"]), (3, 1))
        self.assertEqual((result["official_gpa"], result["model_status"]), (2.0, "reproduced"))

    def test_all_grades_without_a_semester_does_not_mention_it(self) -> None:
        self.assertNotIn("semester_ignored", self.app.export_table("all_grades"))

    def test_all_grades_needs_the_graduation_record(self) -> None:
        self.mocks["get_graduation_remaining"].return_value = {"statusCode": 1}
        with self.assertRaises(NinovaError):
            self.app.export_table("all_grades")

    def test_weekly_schedule(self) -> None:
        result = self.app.export_table("weekly_schedule")
        self.assertEqual(result["columns"],
                         ["Gün", "Başlangıç", "Bitiş", "Ders Kodu", "Ders Adı", "CRN", "Bina", "Derslik"])
        self.assertEqual(result["suggested_filename"], "itu-haftalik-program-202510.csv")
        self.assertEqual(parse(result["csv"])[1:], [
            ["Pazartesi", "13:30", "15:20", "ABC 101E", "Aygıtlara Giriş", "31001", "MED", "A21"],
            ["Salı", "08:30", "10:20", "ABC 300", "Çizge Kuramı", "31003", "EEB", "5102"],
        ])
        self.mocks["get_graduation_remaining"].assert_not_called()

    def test_deadlines_come_from_the_snapshot_without_a_sync(self) -> None:
        self.write_tracking_state(datetime.now(tz=UTC) - timedelta(minutes=5))
        with patch.object(self.app, "sync_all_courses") as sync:
            result = self.app.export_table("deadlines")
        sync.assert_not_called()
        self.assertEqual(result["columns"],
                         ["Ders Kodu", "Ders", "Ödev", "Son Teslim (ISO)", "Son Teslim", "Yüklendi"])
        rows = parse(result["csv"])
        self.assertEqual(len(rows), 3)  # the undated assignment is not a deadline
        self.assertEqual(rows[1], ["ABC 300", "Çizge Kuramı", "Ödev 1", "2024-03-01T23:30:00+03:00",
                                   "01 Mart 2024 23:30", "Evet"])
        self.assertEqual((rows[2][2], rows[2][5]), ("'@Ödev 2", "Hayır"))
        self.assertEqual((result["status"], result["stale"], result["upcoming_count"]), ("ok", False, 1))
        self.assertIsNotNone(result["snapshot_age_hours"])
        self.assertNotIn("message", result)

    def test_stale_deadlines_are_exported_and_flagged(self) -> None:
        self.write_tracking_state(datetime.now(tz=UTC) - timedelta(days=30))
        with patch.object(self.app, "sync_all_courses") as sync:
            result = self.app.export_table("deadlines")
        sync.assert_not_called()
        self.assertEqual((result["status"], result["stale"], result["row_count"]), ("stale_snapshot", True, 2))
        self.assertGreater(result["snapshot_age_hours"], 24 * 29)
        self.assertIn("sync_all_courses", result["message"])

    def test_deadlines_without_a_snapshot(self) -> None:
        with patch.object(self.app, "sync_all_courses") as sync:
            result = self.app.export_table("deadlines")
        sync.assert_not_called()
        self.assertEqual((result["status"], result["stale"], result["row_count"]), ("no_snapshot", True, 0))
        self.assertIsNone(result["snapshot_age_hours"])
        self.assertEqual(parse(result["csv"]), [result["columns"]])

    # -------------------------------------------------------------- delivery

    def test_inline_text_is_bounded(self) -> None:
        many = {"statusCode": 0, "kayitSinifResultList": [
            {"bransKodu": "ABC", "dersKodu": str(100 + index), "dersAdiTR": "Ders " + "x" * 40, "kredi": 3}
            for index in range(50)
        ]}
        self.mocks["list_registered_courses"].return_value = many
        with patch.object(table_export, "MAX_INLINE_BYTES", 1000):
            result = self.app.export_table("term_grades")
        self.assertIs(result["truncated"], True)
        self.assertEqual(result["total_row_count"], 50)
        self.assertLess(result["row_count"], 50)
        self.assertLessEqual(len(result["csv"].encode("utf-8")), 1000)
        self.assertEqual(len(parse(result["csv"])), result["row_count"] + 1)

    def test_save_writes_a_new_file_in_the_exports_directory(self) -> None:
        exports = (Path(self.temp_dir.name) / "downloads" / "exports").resolve()
        with patch.object(table_export, "MAX_INLINE_BYTES", 10):  # a saved file is never cut
            first = self.app.export_table("term_grades", save=True)
            second = self.app.export_table("term_grades", save=True)
        self.assertNotIn("csv", first)
        self.assertNotIn("truncated", first)
        self.assertEqual(Path(first["path"]), exports / "itu-donem-notlari-202510.csv")
        self.assertEqual(Path(second["path"]), exports / "itu-donem-notlari-202510-2.csv")
        data = Path(first["path"]).read_bytes()
        self.assertEqual(first["size_bytes"], len(data))
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(parse(data.decode("utf-8-sig"))[1], ["ABC 101E", "Aygıtlara Giriş", "4", "CC"])
        self.assertEqual(Path(second["path"]).read_bytes(), data)
        self.assertEqual(first["row_count"], 2)

    def test_bad_arguments_are_refused_before_any_read(self) -> None:
        for kwargs in ({"dataset": "attendance"}, {"dataset": "term_grades", "semester": True},
                       {"dataset": "term_grades", "save": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.app.export_table(**kwargs)
        for mock in self.mocks.values():
            mock.assert_not_called()

    # ---------------------------------------------------------- registration

    def test_registration(self) -> None:
        meta = next(tool for tool in TOOLS if tool["name"] == NAME)
        self.assertEqual(meta["inputSchema"]["properties"]["dataset"]["enum"],
                         ["term_grades", "all_grades", "weekly_schedule", "deadlines"])
        self.assertEqual(meta["inputSchema"]["required"], ["dataset"])
        self.assertEqual(TOOL_TO_TOOLSET[NAME], "planning")
        # It can write a file on the host, so it is not served remotely and not advertised as read-only.
        self.assertNotIn(NAME, REMOTE_TOOL_NAMES)
        self.assertIn(NAME, STATEFUL_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
