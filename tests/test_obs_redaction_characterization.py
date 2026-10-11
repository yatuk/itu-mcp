"""Pins what ``redact_obs_profile`` hides, what it reports and the operator gate."""

from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

from ninova_mcp.obs_client import REDACTED, redact_obs_profile
from ninova_mcp.server import NinovaMcpApp

NOTE = (
    "The fields named in redacted_fields exist and are hidden. They are returned only when the "
    "operator set NINOVA_ALLOW_SENSITIVE_OUTPUT and include_sensitive=true is passed."
)
# Field names in the OBS naming style; the values are placeholders.
HIDDEN_KEYS = (
    "kimlikNo", "tcKimlikNo", "TCKN", "pasaportNo", "passportNumber", "ituEPosta", "email", "e_mail",
    "mailAdresi", "telefon", "cepTelefonu", "phone", "gsmNo", "mobil", "cepNo", "cepTel", "tel", "telNo",
    "hotel", "adres", "address", "ikametgah", "İkamet", "dogumTarihi", "doğumYeri", "birthDate", "anaAdi",
    "anneAdi", "babaAdi", "motherName", "fatherName",
)
VISIBLE_KEYS = (
    "E-Posta", "adSoyad", "ogrenciNo", "uyruk", "cinsiyet", "telif", "model", "soyad", "ad", "kimlik",
    "iban", "fotograf", "Tel No", "tel_no", "kartNo",
)


class RedactedKeySetTests(unittest.TestCase):
    def test_exact_set_of_hidden_field_names(self) -> None:
        payload = {key: "value" for key in HIDDEN_KEYS + VISIBLE_KEYS}
        result = redact_obs_profile(payload)
        for key in HIDDEN_KEYS:
            with self.subTest(hidden=key):
                self.assertEqual(result[key], REDACTED)
        for key in VISIBLE_KEYS:
            with self.subTest(visible=key):
                self.assertEqual(result[key], "value")
        self.assertEqual(result["redacted_fields"], list(HIDDEN_KEYS))
        self.assertEqual(result["redaction_note"], NOTE)
        self.assertEqual(list(result)[-2:], ["redacted_fields", "redaction_note"])

    def test_marker_text(self) -> None:
        self.assertEqual(REDACTED, "***REDACTED***")

    def test_empty_values_stay_and_other_falsy_values_are_hidden(self) -> None:
        result = redact_obs_profile({"mail": None, "tel": "", "adres": [], "phone": {}, "gsm": 0, "mobil": False,
                                     "email": [1], "ikamet": {"il": "Örnek"}})
        self.assertEqual(result, {
            "mail": None, "tel": "", "adres": [], "phone": {}, "gsm": REDACTED, "mobil": REDACTED,
            "email": REDACTED, "ikamet": REDACTED,
            "redacted_fields": ["gsm", "mobil", "email", "ikamet"], "redaction_note": NOTE,
        })


class RedactedFieldsTests(unittest.TestCase):
    def test_paths_are_dotted_listed_once_and_in_order_of_discovery(self) -> None:
        payload = {
            "a": {"b": {"tel": "1", "c": [{"mail": "2"}, {"mail": "3"}, {"d": {"adres": "4"}}]}},
            "tel": "5",
            7: {"tel": "6"},
        }
        result = redact_obs_profile(payload)
        self.assertEqual(result["redacted_fields"], ["a.b.tel", "a.b.c.mail", "a.b.c.d.adres", "tel", "7.tel"])
        self.assertEqual(result["a"]["b"]["c"], [{"mail": REDACTED}, {"mail": REDACTED}, {"d": {"adres": REDACTED}}])
        self.assertEqual(result[7], {"tel": REDACTED})

    def test_nothing_hidden_means_an_empty_list_and_no_note(self) -> None:
        self.assertEqual(redact_obs_profile({}), {"redacted_fields": []})
        self.assertEqual(redact_obs_profile({"a": [1, None, "x", {"b": 2.5}]}),
                         {"a": [1, None, "x", {"b": 2.5}], "redacted_fields": []})

    def test_existing_report_keys_are_overwritten(self) -> None:
        result = redact_obs_profile({"redacted_fields": ["old"], "redaction_note": "old"})
        self.assertEqual(result, {"redacted_fields": [], "redaction_note": "old"})

    def test_a_payload_that_is_not_a_record_is_copied_unchanged(self) -> None:
        for payload in ("text a@b.co", None, 5, ["a@b.co", {"tel": "1"}]):
            with self.subTest(payload=payload):
                self.assertEqual(redact_obs_profile(payload), payload)  # type: ignore[arg-type]
        nested = [{"tel": "1"}]
        self.assertIsNot(redact_obs_profile(nested), nested)  # type: ignore[arg-type]

    def test_the_input_is_never_modified(self) -> None:
        payload = {"a": {"tel": "1", "notes": ["x a@b.co"]}, "iletisimBilgiList": [{"iletisimTipiAdiTR": "Cep", "degeri": "1"}]}
        before = deepcopy(payload)
        redact_obs_profile(payload)
        self.assertEqual(payload, before)


class FreeTextEmailTests(unittest.TestCase):
    def test_every_address_in_a_text_is_masked_and_the_rest_is_kept(self) -> None:
        result = redact_obs_profile({"notlar": "Yaz: ilk.kisi+etiket@example.org, sonra ikinci_kisi@alt.example.edu.tr."})
        self.assertEqual(result["notlar"], f"Yaz: {REDACTED}, sonra {REDACTED}.")
        self.assertEqual(result["redacted_fields"], ["notlar"])

    def test_text_inside_lists_is_reported_under_the_list_field(self) -> None:
        result = redact_obs_profile({"a": ["x a@b.co y", "plain", {"b": "c@d.ee"}], "c": [["e@f.gg"]]})
        self.assertEqual(result["a"], [f"x {REDACTED} y", "plain", {"b": REDACTED}])
        self.assertEqual(result["c"], [[REDACTED]])
        self.assertEqual(result["redacted_fields"], ["a", "a.b", "c"])

    def test_what_is_not_an_address_is_left_alone(self) -> None:
        for text in ("a@b", "@example.org", "kisi at example.org", "a @b.co"):
            with self.subTest(text=text):
                self.assertEqual(redact_obs_profile({"notlar": text}), {"notlar": text, "redacted_fields": []})


class ContactRowTests(unittest.TestCase):
    def rows(self, rows: object) -> dict:
        return redact_obs_profile({"iletisimBilgiList": rows})  # type: ignore[dict-item]

    def test_rows_are_hidden_by_their_type_label(self) -> None:
        for label in ("Telefon", "Phone", "Cep", "MOBILE", "gsm", "Ev Adresi", "Address", "ikamet", "E-Posta",
                      "eposta", "E-Mail", "Email", "Mail"):
            with self.subTest(label=label):
                result = self.rows([{"iletisimTipiAdiTR": label, "degeri": "x"}])
                self.assertEqual(result["iletisimBilgiList"], [{"iletisimTipiAdiTR": label, "degeri": REDACTED}])
                self.assertEqual(result["redacted_fields"], [f"iletisimBilgiList: {label}"])
                self.assertEqual(result["redaction_note"], NOTE)

    def test_other_rows_stay_unless_the_value_is_an_address(self) -> None:
        result = self.rows([
            {"iletisimTipiAdiTR": "Web", "degeri": "https://example.org"},
            {"iletisimTipiAdiTR": "Web", "degeri": "kisi@example.org"},
            {"degeri": "x@y.zz"},
            {"iletisimTipiAdiTR": "", "iletisimTipiAdiEN": "Fax", "degeri": "5"},
        ])
        self.assertEqual([row["degeri"] for row in result["iletisimBilgiList"]],
                         ["https://example.org", REDACTED, REDACTED, "5"])
        self.assertEqual(result["redacted_fields"], ["iletisimBilgiList: Web", "iletisimBilgiList: unlabelled"])

    def test_turkish_label_is_used_before_the_english_one(self) -> None:
        result = self.rows([
            {"iletisimTipiAdiTR": "Web", "iletisimTipiAdiEN": "Phone", "degeri": "5"},
            {"iletisimTipiAdiTR": None, "iletisimTipiAdiEN": "GSM", "degeri": 5},
            {"iletisimTipiAdiTR": "  Cep   Tel ", "degeri": "6"},
        ])
        self.assertEqual([row["degeri"] for row in result["iletisimBilgiList"]], ["5", REDACTED, REDACTED])
        self.assertEqual(result["redacted_fields"], ["iletisimBilgiList: GSM", "iletisimBilgiList: Cep Tel"])

    def test_empty_values_are_kept_and_not_reported(self) -> None:
        rows = [{"iletisimTipiAdiTR": "Adres", "degeri": ""}, {"iletisimTipiAdiTR": "Cep", "degeri": None},
                {"iletisimTipiAdiTR": "Cep", "degeri": 0}, {"iletisimTipiAdiTR": "Cep"}]
        result = self.rows(rows)
        self.assertEqual(result["iletisimBilgiList"], rows)
        self.assertEqual(result["redacted_fields"], [])
        self.assertNotIn("redaction_note", result)

    def test_nested_groups_single_records_and_odd_members(self) -> None:
        result = self.rows([
            {"iletisimBilgiList": [{"iletisimTipiAdiTR": "Cep", "degeri": "1"}], "iletisimTipiAdiTR": "Cep", "degeri": "kept"},
            [{"iletisimTipiAdiTR": "Cep", "degeri": "2"}],
            "text a@b.co", None, 5,
            {"iletisimBilgiList": None},
        ])
        self.assertEqual(result["iletisimBilgiList"], [
            {"iletisimBilgiList": [{"iletisimTipiAdiTR": "Cep", "degeri": REDACTED}], "iletisimTipiAdiTR": "Cep", "degeri": "kept"},
            [{"iletisimTipiAdiTR": "Cep", "degeri": REDACTED}],
            "text a@b.co", None, 5,
            {"iletisimBilgiList": None},
        ])
        self.assertEqual(result["redacted_fields"], ["iletisimBilgiList: Cep"])
        single = redact_obs_profile({"x": {"iletisimBilgiList": {"iletisimTipiAdiEN": "Phone", "degeri": "1"}}})
        self.assertEqual(single["x"]["iletisimBilgiList"]["degeri"], REDACTED)
        self.assertEqual(single["redacted_fields"], ["iletisimBilgiList: Phone"])

    def test_only_the_value_of_a_contact_row_is_examined(self) -> None:
        # Current behaviour, pinned as it is: other fields of a row pass through,
        # and a label is matched after casefold only (so "İkamet" is not recognised).
        rows = [{"iletisimTipiAdiTR": "Web", "degeri": "x", "email": "z@z.zz"},
                {"iletisimTipiAdiTR": "İkamet", "degeri": "q"}]
        result = self.rows(rows)
        self.assertEqual(result["iletisimBilgiList"], rows)
        self.assertEqual(result["redacted_fields"], [])


class IncludeSensitiveGateTests(unittest.TestCase):
    PAYLOAD = {"kisiselBilgiler": {"adSoyad": "Deniz Örnek", "cepTelefonu": "+90 500 000 00 00"},
               "iletisimBilgiList": [{"iletisimTipiAdiTR": "Cep", "degeri": "+90 500 000 00 01"}]}

    def app(self) -> NinovaMcpApp:
        app = NinovaMcpApp()
        app._client = MagicMock()
        app._obs = MagicMock()
        app._obs.get_profile.return_value = deepcopy(self.PAYLOAD)
        app._obs.get_contacts.return_value = deepcopy(self.PAYLOAD)
        return app

    def test_redaction_runs_unless_both_the_operator_and_the_caller_ask(self) -> None:
        for switch, include, hidden in (("0", False, True), ("0", True, True), ("1", False, True), ("1", True, False),
                                        ("", True, True)):
            for tool in ("obs_get_profile", "obs_get_contacts"):
                with self.subTest(switch=switch, include=include, tool=tool):
                    with patch.dict("os.environ", {"NINOVA_ALLOW_SENSITIVE_OUTPUT": switch}):
                        result = getattr(self.app(), tool)(include_sensitive=include)
                    if hidden:
                        self.assertEqual(result["kisiselBilgiler"]["cepTelefonu"], REDACTED)
                        self.assertEqual(result["iletisimBilgiList"][0]["degeri"], REDACTED)
                        self.assertEqual(result["redacted_fields"], ["kisiselBilgiler.cepTelefonu", "iletisimBilgiList: Cep"])
                    else:
                        self.assertEqual(result["kisiselBilgiler"]["cepTelefonu"], "+90 500 000 00 00")
                        self.assertEqual(result["iletisimBilgiList"][0]["degeri"], "+90 500 000 00 01")
                        self.assertNotIn("redacted_fields", result)

    def test_the_tools_call_the_redaction_of_the_client_module(self) -> None:
        # app_obs holds its own reference to the function; it must be the one
        # ninova_mcp.obs_client exposes.
        import ninova_mcp.app_obs as app_obs
        import ninova_mcp.obs_client as obs_client

        self.assertIs(app_obs.redact_obs_profile, obs_client.redact_obs_profile)


if __name__ == "__main__":
    unittest.main()
