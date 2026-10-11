"""Shuttle and sports hours against trimmed copies of the real SKS pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unittest

import requests

from ninova_mcp.public_client import ItuPublicClient, ItuPublicError

FIXTURES = Path(__file__).parent / "fixtures"
RING_URL = "https://sks.itu.edu.tr/kampus-ici-ring-servis"
OLD_RING_URL = "https://sks.itu.edu.tr/mekik-servis"
SPORTS_URL = "https://sks.itu.edu.tr/hizmetlerimiz/spor-hizmetleri/spor-tesisleri-saatleri"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeSession:
    """Answers the listed URLs; anything else is a 404, as the old shuttle address is now."""

    def __init__(self, routes: dict[str, str]) -> None:
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requested.append(url)
        body = self.routes.get(url)
        response = requests.Response()
        response.status_code = 200 if body is not None else 404
        response.url = url
        response._content = (body or "not found").encode("utf-8")
        response.encoding = "utf-8"
        return response


def make_client(routes: dict[str, str]) -> tuple[ItuPublicClient, FakeSession]:
    session = FakeSession(routes)
    client = ItuPublicClient(session=session, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    return client, session


class ShuttleScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.session = make_client({RING_URL: fixture("sks_ring_servis.html")})

    def test_shuttle_is_read_from_the_page_the_site_links_to(self) -> None:
        result = self.client.get_shuttle_schedule()
        self.assertEqual(self.session.requested, [RING_URL])
        self.assertNotIn(OLD_RING_URL, self.session.requested)
        self.assertEqual(result["url"], RING_URL)
        self.assertEqual(result["source"], "sks.itu.edu.tr/kampus-ici-ring-servis")
        self.assertNotIn("parse_warning", result)
        self.assertEqual(
            [item["title"] for item in result["schedules"]],
            [
                "KAMPÜS İÇİ RİNG SERVİS SAATLERİ (HAFTA İÇİ)",
                "KAMPÜS İÇİ RİNG SERVİS SAATLERİ (HAFTA SONU)",
                "KAMU KONUTLARI SERVİS SAATLERİ (HAFTA İÇİ)",
                "KYK MÜHENDİSHANE YURDU SERVİS SAATLERİ (HAFTA İÇİ)",
                "KYK MÜHENDİSHANE YURDU SERVİS SAATLERİ (HAFTA SONU)",
            ],
        )
        self.assertEqual(result["stop_list_count"], 3)
        weekday = result["schedules"][0]["rows"]
        self.assertEqual(weekday[0], ["İMKB (VADİ) YURTLARINDAN HAREKET", "GÖLET YURTLARINDAN HAREKET (MED-B)"])
        self.assertIn(["9:30", "9:30"], weekday)
        self.assertEqual(result["stop_lists"][0]["rows"][1], ["1", "İMKB (Vadi) Yurtları", "1", "Gölet Yurtları (MED-B)"])

    def test_page_states_from_when_the_timetable_applies(self) -> None:
        note = self.client.get_shuttle_schedule()["validity_note"]
        self.assertIn("25 Eylül 2026 tarihinden itibaren", note)
        self.assertLess(len(note), 400)

    def test_filters_work_on_the_real_tables(self) -> None:
        weekend = self.client.get_shuttle_schedule(day_type="hafta sonu")
        self.assertEqual(weekend["schedule_count"], 2)
        self.assertTrue(all("HAFTA SONU" in item["title"] for item in weekend["schedules"]))
        kyk = self.client.get_shuttle_schedule(route="kyk mühendishane")
        self.assertEqual((kyk["schedule_count"], kyk["stop_list_count"]), (2, 1))
        self.assertEqual(len(self.session.requested), 1)

    def test_missing_page_is_still_a_clear_error(self) -> None:
        client, _ = make_client({})
        with self.assertRaises(ItuPublicError) as caught:
            client.get_shuttle_schedule()
        self.assertIn("404", str(caught.exception))


class SportsFacilityHoursTests(unittest.TestCase):
    def test_real_table_is_read(self) -> None:
        client, session = make_client({SPORTS_URL: fixture("sks_spor_tesisleri_saatleri.html")})
        result = client.get_sports_facility_hours()
        self.assertEqual(session.requested, [SPORTS_URL])
        self.assertEqual(result["count"], 7)
        self.assertNotIn("parse_warning", result)
        pool = client.get_sports_facility_hours("yüzme")["facilities"]
        self.assertEqual(pool, [{
            "facility": "Olimpik Yüzme Havuzu",
            "weekday": {"opens": "08:00", "closes": "20:00", "closed": False},
            "weekend": {"opens": "08:00", "closes": "20:00", "closed": False},
            "closed_days": ["Pazartesi"],
            "note": "Pazartesi Kapalı",
        }])
        closed = client.get_sports_facility_hours("gümüşsuyu")["facilities"][0]
        self.assertEqual(closed["weekend"], {"opens": None, "closes": None, "closed": True})


if __name__ == "__main__":
    unittest.main()
