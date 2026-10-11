"""Profile redaction must cover what the contacts redaction covers."""

from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import MagicMock, patch

from ninova_mcp.obs_client import redact_obs_profile

REDACTED = "***REDACTED***"
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tool_metadata.obs import OBS_TOOLS

# Entirely invented person. Field names follow the OBS naming style.
PROFILE = {
    "statusCode": 0,
    "kisiselBilgiler": {
        "adSoyad": "Deniz Örnek",
        "ogrenciNo": "990990990",
        "kimlikNo": "10000000146",
        "pasaportNo": "X0000001",
        "ituEPosta": "ornekd99@example.edu.tr",
        "kisiselEmail": "deniz.ornek@example.org",
        "cepTelefonu": "+90 500 000 00 00",
        "evTelefon": 2120000000,
        "ikametAdresi": "Örnek Sokak No 1",
        "dogumTarihi": "1999-09-09T00:00:00",
        "dogumYeri": "Örnekköy",
        "babaAdi": "Örnek Baba",
        "anaAdi": "Örnek Ana",
        "bolumAdiTR": "Örnek Mühendisliği",
        "notlar": "Ulaşmak için ornek.baska@example.org adresini kullanın.",
        "uyruk": "Örnek",
        "bosAlan": None,
        "bosTelefon": "",
    },
    "iletisimBilgiList": [{"iletisimBilgiList": [
        {"iletisimTipiAdiTR": "Cep Telefonu", "degeri": "+90 500 000 00 01"},
        {"iletisimTipiAdiTR": "Ev Adresi", "degeri": "Örnek Mahallesi No 2"},
        {"iletisimTipiAdiTR": "E-posta", "degeri": "iletisim.ornek@example.org"},
        {"iletisimTipiAdiEN": "E-Mail Address", "degeri": "second.ornek@example.org"},
        {"iletisimTipiAdiTR": "Web", "degeri": "https://example.org/ornek"},
        "not a record",
    ]}],
}
HIDDEN = (
    "10000000146", "X0000001", "ornekd99@example.edu.tr", "deniz.ornek@example.org", "+90 500 000 00 00",
    "2120000000", "Örnek Sokak No 1", "1999-09-09", "Örnekköy", "Örnek Baba", "Örnek Ana",
    "ornek.baska@example.org", "+90 500 000 00 01", "Örnek Mahallesi No 2", "iletisim.ornek@example.org",
    "second.ornek@example.org",
)


class ProfileRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redacted = redact_obs_profile(PROFILE)
        self.rendered = json.dumps(self.redacted, ensure_ascii=False)

    def test_emails_phones_addresses_birth_and_ids_are_hidden(self) -> None:
        for value in HIDDEN:
            with self.subTest(value=value):
                self.assertNotIn(value, self.rendered)
        personal = self.redacted["kisiselBilgiler"]
        for key in ("kimlikNo", "pasaportNo", "ituEPosta", "kisiselEmail", "cepTelefonu", "evTelefon",
                    "ikametAdresi", "dogumTarihi", "dogumYeri", "babaAdi", "anaAdi"):
            with self.subTest(key=key):
                self.assertEqual(personal[key], REDACTED)

    def test_name_student_number_and_program_stay_visible(self) -> None:
        personal = self.redacted["kisiselBilgiler"]
        self.assertEqual(personal["adSoyad"], "Deniz Örnek")
        self.assertEqual(personal["ogrenciNo"], "990990990")
        self.assertEqual(personal["bolumAdiTR"], "Örnek Mühendisliği")
        self.assertEqual(personal["uyruk"], "Örnek")

    def test_an_address_inside_free_text_is_masked_without_dropping_the_text(self) -> None:
        note = self.redacted["kisiselBilgiler"]["notlar"]
        self.assertIn("Ulaşmak için", note)
        self.assertIn(REDACTED, note)

    def test_empty_values_are_left_empty_and_not_listed(self) -> None:
        personal = self.redacted["kisiselBilgiler"]
        self.assertIsNone(personal["bosAlan"])
        self.assertEqual(personal["bosTelefon"], "")
        self.assertNotIn("kisiselBilgiler.bosTelefon", self.redacted["redacted_fields"])

    def test_hidden_fields_are_named(self) -> None:
        names = self.redacted["redacted_fields"]
        for name in ("kisiselBilgiler.kimlikNo", "kisiselBilgiler.ituEPosta", "kisiselBilgiler.cepTelefonu",
                     "kisiselBilgiler.ikametAdresi", "kisiselBilgiler.dogumTarihi", "kisiselBilgiler.dogumYeri",
                     "kisiselBilgiler.notlar", "iletisimBilgiList: Cep Telefonu", "iletisimBilgiList: Ev Adresi",
                     "iletisimBilgiList: E-posta", "iletisimBilgiList: E-Mail Address"):
            with self.subTest(name=name):
                self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("NINOVA_ALLOW_SENSITIVE_OUTPUT", self.redacted["redaction_note"])

    def test_contact_rows_keep_their_type_and_unrelated_rows(self) -> None:
        rows = self.redacted["iletisimBilgiList"][0]["iletisimBilgiList"]
        self.assertEqual(rows[0], {"iletisimTipiAdiTR": "Cep Telefonu", "degeri": REDACTED})
        self.assertEqual(rows[2]["degeri"], REDACTED)
        self.assertEqual(rows[4]["degeri"], "https://example.org/ornek")
        self.assertEqual(rows[5], "not a record")

    def test_input_is_not_modified(self) -> None:
        original = deepcopy(PROFILE)
        redact_obs_profile(PROFILE)
        self.assertEqual(PROFILE, original)

    def test_payload_with_nothing_sensitive_lists_nothing(self) -> None:
        result = redact_obs_profile({"kisiselBilgiler": {"adSoyad": "Deniz Örnek"}})
        self.assertEqual(result["redacted_fields"], [])
        self.assertNotIn("redaction_note", result)

    def test_unexpected_shapes_do_not_raise(self) -> None:
        for payload in ({}, {"kisiselBilgiler": None}, {"iletisimBilgiList": [None, {"iletisimBilgiList": None}]},
                        {"kisiselBilgiler": {"telefonlar": ["+90 500 000 00 02"], "adresBilgisi": {"il": "Örnek"}}}):
            with self.subTest(payload=payload):
                rendered = json.dumps(redact_obs_profile(payload), ensure_ascii=False)
                self.assertNotIn("+90 500 000 00 02", rendered)
                self.assertNotIn('"il"', rendered)


class ProfileToolTests(unittest.TestCase):
    def make(self) -> NinovaMcpApp:
        app = NinovaMcpApp()
        app._client = MagicMock()
        app._obs = MagicMock()
        app._obs.get_profile.return_value = deepcopy(PROFILE)
        app._obs.get_contacts.return_value = deepcopy(PROFILE)
        return app

    def test_profile_and_contacts_hide_the_same_things(self) -> None:
        with patch.dict("os.environ", {"NINOVA_ALLOW_SENSITIVE_OUTPUT": "0"}):
            app = self.make()
            for tool in (app.obs_get_profile, app.obs_get_contacts):
                for include in (False, True):
                    with self.subTest(tool=tool.__name__, include=include):
                        result = tool(include_sensitive=include)
                        rendered = json.dumps(result, ensure_ascii=False)
                        for value in HIDDEN:
                            self.assertNotIn(value, rendered)
                        self.assertIn("kisiselBilgiler.ituEPosta", result["redacted_fields"])
                        self.assertEqual(result["kisiselBilgiler"]["adSoyad"], "Deniz Örnek")

    def test_operator_switch_still_reveals_everything(self) -> None:
        with patch.dict("os.environ", {"NINOVA_ALLOW_SENSITIVE_OUTPUT": "1"}):
            result = self.make().obs_get_profile(include_sensitive=True)
        rendered = json.dumps(result, ensure_ascii=False)
        for value in HIDDEN:
            self.assertIn(value, rendered)
        self.assertNotIn("redacted_fields", result)

    def test_description_names_what_is_hidden(self) -> None:
        tool = next(tool for tool in OBS_TOOLS if tool["name"] == "obs_get_profile")
        for word in ("e-mail", "phone", "address", "birth", "redacted_fields"):
            self.assertIn(word, tool["description"])


if __name__ == "__main__":
    unittest.main()
