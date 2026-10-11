"""Draft meetings must name the room, and calendar rows the whole course code."""

from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

from ninova_mcp.obs_client import ObsClient
from ninova_mcp.registration_draft import normalize_calendar

from test_registration_draft import CALENDAR, DRAFT, PERIOD

# Synthetic calendar in the shape seen live: the branch and the number arrive
# apart, and the room type sits next to the door label.
LIVE_CALENDAR = {
    "statusCode": 0,
    "kayitSinifResultList": [{
        "crn": 11739,
        "bransKodu": "UCK",
        "dersKodu": "468E",
        "dersAdiEN": "Aircraft Design",
        "sinifYerZaman": [
            {"gunAdiEN": "Monday", "baslangicSaati": 570, "bitisSaati": 629, "mekanAdi": "Amfi",
             "mekanKapiTabelasi": "Z 12", "binaAdi": "Example Hall", "kampusAdi": "Main campus"},
            {"gunAdiEN": "Thursday", "baslangicSaati": 690, "bitisSaati": 749, "mekanAdi": "Derslik",
             "mekanKapiTabelasi": " 4407 ", "binaAdi": "Example Hall", "kampusAdi": "Main campus"},
        ],
    }],
}


def meeting(**fields: object) -> dict:
    calendar = deepcopy(LIVE_CALENDAR)
    entry = calendar["kayitSinifResultList"][0]["sinifYerZaman"][0]
    for key, value in fields.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return normalize_calendar(calendar)[0]["sessions"][0]


class RoomTests(unittest.TestCase):
    def test_door_label_is_the_room_and_the_type_is_kept_apart(self) -> None:
        first, second = normalize_calendar(LIVE_CALENDAR)[0]["sessions"]
        self.assertEqual((first["room"], first["room_type"]), ("Z 12", "Amfi"))
        self.assertEqual((second["room"], second["room_type"]), ("4407", "Derslik"))
        self.assertEqual(first["room_source"], "mekanKapiTabelasi")
        self.assertEqual(first["building"], "Example Hall")

    def test_missing_or_blank_door_label_falls_back_to_the_old_field(self) -> None:
        for label in (None, "", "   "):
            with self.subTest(label=label):
                session = meeting(mekanKapiTabelasi=label)
                self.assertEqual(session["room"], "Amfi")
                self.assertEqual(session["room_source"], "mekanAdi")
                self.assertIsNone(session["room_type"])

    def test_no_room_information_at_all(self) -> None:
        session = meeting(mekanKapiTabelasi=None, mekanAdi=None)
        self.assertIsNone(session["room"])
        self.assertIsNone(session["room_type"])
        self.assertIsNone(session["room_source"])

    def test_numeric_door_label_is_text(self) -> None:
        self.assertEqual(meeting(mekanKapiTabelasi=4407)["room"], "4407")

    def test_existing_fixture_shape_keeps_its_room(self) -> None:
        self.assertEqual(normalize_calendar(CALENDAR)[0]["sessions"][0]["room"], "Room 101")


class CalendarCodeTests(unittest.TestCase):
    def test_branch_and_number_are_joined(self) -> None:
        self.assertEqual(normalize_calendar(LIVE_CALENDAR)[0]["code"], "UCK 468E")

    def test_a_code_that_is_already_whole_is_left_alone(self) -> None:
        self.assertEqual(normalize_calendar(CALENDAR)[0]["code"], "UCK 468E")
        calendar = deepcopy(LIVE_CALENDAR)
        calendar["kayitSinifResultList"][0]["dersKodu"] = "UCK 468E"
        self.assertEqual(normalize_calendar(calendar)[0]["code"], "UCK 468E")

    def test_number_alone_is_completed_from_the_saved_draft_row(self) -> None:
        calendar = deepcopy(LIVE_CALENDAR)
        calendar["kayitSinifResultList"][0].pop("bransKodu")
        self.assertEqual(normalize_calendar(calendar)[0]["code"], "468E")
        client = ObsClient(ninova_client=MagicMock())
        with patch.object(client, "api_get", side_effect=[deepcopy(PERIOD), deepcopy(DRAFT), calendar]):
            result = client.get_registration_draft()
        self.assertEqual(result["calendar"][0]["code"], "UCK 468E")
        self.assertEqual(result["courses"][0]["course_code"], "UCK 468E")

    def test_draft_read_carries_rooms_into_course_sessions(self) -> None:
        client = ObsClient(ninova_client=MagicMock())
        with patch.object(client, "api_get", side_effect=[deepcopy(PERIOD), deepcopy(DRAFT), deepcopy(LIVE_CALENDAR)]):
            result = client.get_registration_draft()
        self.assertEqual([session["room"] for session in result["courses"][0]["sessions"]], ["Z 12", "4407"])
        self.assertEqual(result["calendar"][0]["code"], "UCK 468E")

    def test_missing_code_stays_none(self) -> None:
        calendar = deepcopy(LIVE_CALENDAR)
        calendar["kayitSinifResultList"][0].pop("bransKodu")
        calendar["kayitSinifResultList"][0].pop("dersKodu")
        self.assertIsNone(normalize_calendar(calendar)[0]["code"])


if __name__ == "__main__":
    unittest.main()
