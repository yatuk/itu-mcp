"""Registration reads and independent validation must remain explicit and read-only."""

from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import MagicMock, call, patch

import requests

from ninova_mcp.client import NinovaAuthError
from ninova_mcp.obs_client import ObsClient, ObsError
from ninova_mcp.registration_draft import (
    DRAFT_PATH,
    VALIDATION_PATH,
    normalize_calendar,
    normalize_reasons,
    normalize_validation,
)


PERIOD = {
    "statusCode": 0,
    "kayitZamanKontrolResult": {
        "akademikDonemKodu": "202710",
        "ogrenciTaslakOlusturabilir": True,
        "sinif": 4,
        "baslangicTarihi": "2026-09-10T10:00:00",
        "bitisTarihi": "2026-09-20T17:00:00",
    },
}
DRAFT = {
    "statusCode": 0,
    "taslakBilgi": {
        "akademikDonemKodu": "202710",
        "akademikDonemAdi": "2026-2027 Fall",
        "kontrolTarihi": "2026-09-13T10:30:00",
        "taslakSinifListesi": [
            {
                "crn": 11739,
                "dersKodu": "UCK 468E",
                "dersAdiEN": "Aircraft Design",
                "sinifAlinabilir": True,
                "kontrolSonucListesi": [],
            },
            {
                "crn": 11730,
                "dersKodu": "UZB 438E",
                "dersAdiEN": "Robotic Control Systems",
                "sinifAlinabilir": False,
                "kontrolSonucListesi": [{"resultCode": "VAL08", "resultData": {"saProgram": "UZBE"}}],
            },
        ],
    },
}
CALENDAR = {
    "statusCode": 0,
    "kayitSinifResultList": [{
        "crn": 11739,
        "dersKodu": "UCK 468E",
        "dersAdiEN": "Aircraft Design",
        "sinifYerZaman": [{
            "gunAdiEN": "Monday",
            "baslangicSaati": 570,
            "bitisSaati": 629,
            "mekanAdi": "Room 101",
            "binaAdi": "Engineering",
            "kampusAdi": "Main campus",
        }],
    }],
}


def validation_payload(*rows: dict) -> dict:
    return {"statusCode": 0, "ecrnResultList": list(rows)}


def validation_row(crn: str = "11739", status: object = 0, details: object = None) -> dict:
    return {
        "crn": crn,
        "statusCode": status,
        "dersKodu": "UCK 468E",
        "dersAdi": "Aircraft Design",
        "kredi": 3,
        "taslakKontrolResultList": [] if details is None else details,
    }


def response(payload: object = None, *, status: int = 200, html: str | None = None) -> MagicMock:
    result = MagicMock()
    result.status_code = status
    result.headers = {"Content-Type": "text/html" if html is not None else "application/json"}
    result.text = html if html is not None else json.dumps(payload)
    result.content = result.text.encode("utf-8")
    result.json.return_value = payload
    return result


class RegistrationDraftReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ObsClient(ninova_client=MagicMock())

    def read(self, draft: object = DRAFT, calendar: object = CALENDAR) -> dict:
        with patch.object(self.client, "api_get", side_effect=[deepcopy(PERIOD), deepcopy(draft), deepcopy(calendar)]):
            return self.client.get_registration_draft()

    def test_reads_actual_payload_shape_and_preserves_saved_verdict_time(self) -> None:
        result = self.read()
        self.assertTrue(result["draft_exists"])
        self.assertEqual(result["term_code"], "202710")
        self.assertEqual(result["course_count"], 2)
        self.assertEqual(result["checked_at"], "2026-09-13T10:30:00")
        self.assertEqual(result["eligibility_source"], "saved_draft")
        first, second = result["courses"]
        self.assertEqual((first["crn"], first["course_code"], first["eligible"]), ("11739", "UCK 468E", True))
        self.assertEqual(second["eligibility_status"], "ineligible")
        self.assertEqual(second["reasons"][0]["code"], "VAL08")
        self.assertIn("program", second["error_reason"])

    def test_uses_draft_registration_period_instead_of_last_registered_term(self) -> None:
        with patch.object(self.client, "api_get", side_effect=[deepcopy(PERIOD), deepcopy(DRAFT), deepcopy(CALENDAR)]) as read:
            with patch.object(self.client, "resolve_semester", return_value={"donemKodu": "202620"}) as resolve:
                self.client.get_registration_draft()
        resolve.assert_not_called()
        self.assertEqual(read.call_args_list, [
            call(DRAFT_PATH + "KayitZamaniKontrolu"),
            call(DRAFT_PATH + "TaslakBilgisi/202710"),
            call(DRAFT_PATH + "TaslakTakvimi/202710"),
        ])

    def test_missing_registration_term_does_not_fall_back_to_old_course_history(self) -> None:
        period = deepcopy(PERIOD)
        period["kayitZamanKontrolResult"].pop("akademikDonemKodu")
        with patch.object(self.client, "api_get", return_value=period) as read:
            with patch.object(self.client, "resolve_semester", return_value={"donemKodu": "202620"}) as resolve:
                with self.assertRaises(ObsError):
                    self.client.get_registration_draft()
        read.assert_called_once_with(DRAFT_PATH + "KayitZamaniKontrolu")
        resolve.assert_not_called()

    def test_missing_draft_is_distinct_from_empty_existing_draft(self) -> None:
        with patch.object(self.client, "api_get", side_effect=[deepcopy(PERIOD), {"statusCode": 0, "taslakBilgi": None}]) as read:
            missing = self.client.get_registration_draft()
        self.assertFalse(missing["draft_exists"])
        self.assertEqual(missing["courses"], [])
        self.assertEqual(read.call_count, 2)
        empty = deepcopy(DRAFT)
        empty["taslakBilgi"]["taslakSinifListesi"] = []
        existing = self.read(empty, {"statusCode": 0, "kayitSinifResultList": []})
        self.assertTrue(existing["draft_exists"])
        self.assertEqual(existing["courses"], [])

    def test_schema_errors_are_never_reported_as_no_draft(self) -> None:
        for payload in (
            "<html>Sign in</html>",
            {"statusCode": False, "taslakBilgi": None},
            {"statusCode": 0},
            {"statusCode": 0, "taslakBilgi": {}},
            {"statusCode": 0, "taslakBilgi": {"taslakSinifListesi": {}}},
            {"statusCode": 1, "resultCode": "ERRKayitZamani"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ObsError):
                self.read(payload)

    def test_rejects_saved_draft_from_another_term(self) -> None:
        draft = deepcopy(DRAFT)
        draft["taslakBilgi"]["akademikDonemKodu"] = "202620"
        with self.assertRaisesRegex(ObsError, "different term"):
            self.read(draft)

    def test_unknown_verdict_does_not_become_eligible_through_truthiness(self) -> None:
        for verdict in (None, "true", "false", 1, 0):
            draft = deepcopy(DRAFT)
            draft["taslakBilgi"]["taslakSinifListesi"][0]["sinifAlinabilir"] = verdict
            with self.subTest(verdict=verdict):
                course = self.read(draft)["courses"][0]
                self.assertIsNone(course["eligible"])
                self.assertEqual(course["eligibility_status"], "unknown")

    def test_saved_success_with_restrictions_is_not_a_reliable_eligible_verdict(self) -> None:
        for details in ([{"resultCode": "VAL08"}], None, "Unreadable details"):
            draft = deepcopy(DRAFT)
            draft["taslakBilgi"]["taslakSinifListesi"][0]["kontrolSonucListesi"] = details
            with self.subTest(details=details):
                course = self.read(draft)["courses"][0]
                self.assertIsNone(course["eligible"])
                self.assertEqual(course["eligibility_status"], "unknown")

    def test_unavailable_course_without_details_still_explains_failure(self) -> None:
        draft = deepcopy(DRAFT)
        row = draft["taslakBilgi"]["taslakSinifListesi"][0]
        row.update({"sinifAlinabilir": False, "kontrolSonucListesi": []})
        course = self.read(draft)["courses"][0]
        self.assertFalse(course["eligible"])
        self.assertTrue(course["error_reason"])

    def test_partial_calendar_keeps_all_courses_and_marks_missing_schedule(self) -> None:
        result = self.read()
        first, second = result["courses"]
        self.assertEqual(first["sessions"][0]["time"], "09:30/10:29")
        self.assertEqual(first["sessions"][0]["day_en"], "Monday")
        self.assertTrue(first["schedule_available"])
        self.assertEqual(second["sessions"], [])
        self.assertFalse(second["schedule_available"])

    def test_calendar_failure_does_not_hide_saved_courses_or_verdicts(self) -> None:
        result = self.read(calendar={"statusCode": 1, "resultCode": "Unavailable"})
        self.assertEqual(result["course_count"], 2)
        self.assertTrue(result["courses"][0]["eligible"])
        self.assertEqual(result["calendar"], [])
        self.assertEqual(result["errors"][0]["scope"], "calendar")

    def test_calendar_network_or_auth_failure_preserves_the_saved_draft(self) -> None:
        for error in (requests.Timeout("Timed out"), requests.ConnectionError("Connection closed"), NinovaAuthError("Session expired")):
            with self.subTest(error=type(error).__name__):
                with patch.object(self.client, "api_get", side_effect=[deepcopy(PERIOD), deepcopy(DRAFT), error]):
                    result = self.client.get_registration_draft()
                self.assertEqual(result["course_count"], 2)
                self.assertTrue(result["courses"][0]["eligible"])
                self.assertEqual(result["errors"][0]["scope"], "calendar")

    def test_malformed_calendar_entry_is_reported_as_partial_failure(self) -> None:
        calendar = deepcopy(CALENDAR)
        calendar["kayitSinifResultList"][0]["sinifYerZaman"] = [None]
        result = self.read(calendar=calendar)
        self.assertEqual(result["course_count"], 2)
        self.assertEqual(result["errors"][0]["scope"], "calendar")

    def test_invalid_clock_values_do_not_claim_available_schedule(self) -> None:
        calendar = deepcopy(CALENDAR)
        calendar["kayitSinifResultList"][0]["sinifYerZaman"][0]["baslangicSaati"] = 2500
        result = self.read(calendar=calendar)
        self.assertFalse(result["courses"][0]["schedule_available"])

    def test_calendar_times_are_minutes_and_invalid_values_stay_unknown(self) -> None:
        calendar = deepcopy(CALENDAR)
        meeting = calendar["kayitSinifResultList"][0]["sinifYerZaman"][0]
        meeting.update({"baslangicSaati": 0, "bitisSaati": 1439})
        converted = normalize_calendar(calendar)[0]["sessions"][0]
        self.assertEqual(converted["time"], "00:00/23:59")
        for invalid in (-1, 1440, 1500, 9.5, True, "570", None):
            meeting["baslangicSaati"] = invalid
            with self.subTest(invalid=invalid):
                converted = normalize_calendar(calendar)[0]["sessions"][0]
                self.assertIsNone(converted["start"])
                self.assertIsNone(converted["time"])

    def test_zero_length_and_reversed_meetings_do_not_claim_available_schedule(self) -> None:
        for end in (570, 540):
            calendar = deepcopy(CALENDAR)
            calendar["kayitSinifResultList"][0]["sinifYerZaman"][0]["bitisSaati"] = end
            with self.subTest(end=end):
                result = self.read(calendar=calendar)
                self.assertFalse(result["courses"][0]["schedule_available"])

    def test_reads_never_recheck_save_delete_or_register_the_draft(self) -> None:
        ninova = self.client.ninova
        ninova._looks_like_login_page.return_value = False
        with patch.object(self.client, "_get_jwt", return_value="test.token.value"):
            with patch.object(self.client, "_safe_request", side_effect=[response(PERIOD), response(DRAFT), response(CALENDAR)]) as request:
                self.client.get_registration_draft()
        self.assertEqual([(args.args[0], args.args[1]) for args in request.call_args_list], [
            ("GET", self.client.base_url + DRAFT_PATH + "KayitZamaniKontrolu"),
            ("GET", self.client.base_url + DRAFT_PATH + "TaslakBilgisi/202710"),
            ("GET", self.client.base_url + DRAFT_PATH + "TaslakTakvimi/202710"),
        ])


class RegistrationValidationTests(unittest.TestCase):
    def test_success_failure_and_omitted_crns_remain_distinct(self) -> None:
        result = normalize_validation(validation_payload(
            validation_row(),
            validation_row("11730", 1, [{"resultCode": "VAL08", "resultData": {"saProgram": "UZBE"}}]),
        ), ["11739", "11730", "11732"])
        self.assertTrue(result["11739"]["eligible"])
        self.assertFalse(result["11730"]["eligible"])
        self.assertFalse(result["11730"]["program_eligible"])
        self.assertIsNone(result["11732"]["eligible"])
        self.assertIn("omitted", result["11732"]["error_reason"])

    def test_conflicting_or_unknown_validation_data_is_not_success(self) -> None:
        rows = [
            validation_row(status="0"),
            validation_row(status=True),
            validation_row(status=2),
            validation_row(details=[{"resultCode": "VAL11"}]),
            validation_row(details="Unreadable validation details"),
        ]
        for row in rows:
            with self.subTest(row=row):
                result = normalize_validation(validation_payload(row), ["11739"])
                self.assertIsNone(result["11739"]["eligible"])

    def test_unexpected_duplicate_and_malformed_response_rows_are_rejected(self) -> None:
        for payload in (
            validation_payload(validation_row("99999")),
            validation_payload(validation_row(), validation_row()),
            validation_payload(None),
            {"statusCode": 0, "ecrnResultList": {}},
            {"statusCode": 1, "resultCode": "ERRKayitZamani"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ObsError):
                normalize_validation(payload, ["11739"])

    def test_unavailable_validation_without_reasons_still_explains_failure(self) -> None:
        result = normalize_validation(validation_payload(validation_row(status=1)), ["11739"])
        self.assertFalse(result["11739"]["eligible"])
        self.assertTrue(result["11739"]["error_reason"])

    def test_unknown_and_malformed_reason_codes_have_safe_explanations(self) -> None:
        for code in ("FUTURE_CODE", None, [], {}):
            with self.subTest(code=code):
                reasons = normalize_reasons([{"resultCode": code}])
                self.assertEqual(len(reasons), 1)
                self.assertTrue(reasons[0]["message"])
                self.assertIsInstance(reasons[0]["code"], str)

    def test_invalid_crns_are_rejected_before_authentication_or_network(self) -> None:
        client = ObsClient(ninova_client=MagicMock())
        with patch.object(client, "_get_jwt") as auth, patch.object(client, "_safe_request") as request:
            for crns in ([], "11739", [11739], [True], ["11739", "11739"], [" 11739"], ["11739\n"], ["１２３４５"], ["123"], ["123456"], [str(10000 + i) for i in range(13)]):
                with self.subTest(crns=crns), self.assertRaises(ObsError):
                    client.validate_registration_crns(crns)
        auth.assert_not_called()
        request.assert_not_called()
        client.ninova.ensure_logged_in.assert_not_called()

    def test_post_validation_retries_with_same_crns_without_saving_draft(self) -> None:
        for first_response in (
            response(status=401),
            response(status=403),
            response(html="<html>Sign in</html>"),
        ):
            with self.subTest(status=first_response.status_code, content_type=first_response.headers):
                ninova = MagicMock()
                ninova._looks_like_login_page.return_value = True
                client = ObsClient(ninova_client=ninova)
                requested = ["11739", "11730"]
                good = response(validation_payload(validation_row(), validation_row("11730")))
                with patch.object(client, "_get_jwt", return_value="test.token.value") as auth:
                    with patch.object(client, "_safe_request", side_effect=[first_response, good]) as request:
                        result = client.validate_registration_crns(requested)
                self.assertEqual(set(result), set(requested))
                self.assertEqual(request.call_count, 2)
                for attempt in request.call_args_list:
                    self.assertEqual(attempt.args, ("POST", client.base_url + VALIDATION_PATH))
                    self.assertEqual(attempt.kwargs["json"], {"ecrn": requested})
                self.assertIn(call(force=True), auth.call_args_list)
                ninova.ensure_logged_in.assert_called_once_with(verify=True)
                self.assertEqual(requested, ["11739", "11730"])

    def test_post_validation_does_not_retry_after_a_server_error(self) -> None:
        client = ObsClient(ninova_client=MagicMock())
        with patch.object(client, "_get_jwt", return_value="test.token.value"):
            with patch.object(client, "_safe_request", return_value=response(status=500)) as request:
                with self.assertRaisesRegex(ObsError, "HTTP 500"):
                    client.validate_registration_crns(["11739"])
        self.assertEqual(request.call_count, 1)
        client.ninova.ensure_logged_in.assert_not_called()

    def test_post_validation_stops_after_one_failed_authentication_retry(self) -> None:
        client = ObsClient(ninova_client=MagicMock())
        with patch.object(client, "_get_jwt", return_value="test.token.value"):
            with patch.object(client, "_safe_request", return_value=response(status=403)) as request:
                with self.assertRaisesRegex(ObsError, "HTTP 403"):
                    client.validate_registration_crns(["11739"])
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
