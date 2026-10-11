"""No e-mail, phone, address or ID may leave the profile and contact redaction.

Every person, number and address here is invented.
"""

from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import MagicMock, patch

from ninova_mcp.obs_client import REDACTED, redact_obs_profile
from ninova_mcp.server import NinovaMcpApp

EMAILS = ("ornek.bir@example.org", "ornek_iki+etiket@alt.example.edu.tr", "UC@EXAMPLE.COM")
PHONES = (
    "+90 500 000 00 10", "+905000000011", "0090 500 000 00 12", "0 500 000 00 13", "05000000014",
    "5000000015", "(500) 000 00 16", "0(500)0000017", "500-000-00-18", "500.000.00.19", "90 500 000 00 20",
    "+90 212 000 00 21", "0212 000 00 22", "(0212) 000 00 23", "2120000024", "0 (312) 000 0025",
    "444 000 00 26",
)
NATIONAL_IDS = ("10000000146", "98765432100")
ADDRESSES = ("Örnek Mahallesi Deneme Sokak No 7 Daire 3", "Sentetik Caddesi 12/4 Örnekköy")
SECRETS = EMAILS + PHONES + NATIONAL_IDS + ADDRESSES

# Spellings of sensitive field names, each paired with a kind of secret.
EMAIL_KEYS = ("eposta", "ePosta", "E-Posta", "e-posta", "e_posta", "E_POSTA", "email", "Email", "EMAİL", "e-mail",
              "E-Mail", "e_mail", "mail", "Mail", "ituEPosta", "kisiselEmail", "mailAdresi", "İtü E-Posta")
PHONE_KEYS = ("telefon", "Telefon", "TELEFON", "telefonNo", "cepTelefonu", "evTelefon", "evtelefon", "tel", "Tel",
              "TEL", "telNo", "tel_no", "Tel No", "tel-no", "gsm", "GSM", "gsmNo", "cep", "Cep", "cepNo", "cep_no",
              "cepTel", "İş Telefonu", "phone", "mobilePhone", "mobil", "mobile")
ADDRESS_KEYS = ("adres", "Adres", "ADRES", "adresi", "evAdresi", "ikamet", "İkamet", "IKAMET", "ıkamet",
                "ikametAdresi", "ikametgah", "İkametgâh Adresi", "address", "homeAddress")
ID_KEYS = ("kimlikNo", "tcKimlikNo", "TCKimlikNo", "TC Kimlik No", "tc_kimlik_no", "TCKN", "tckn", "pasaportNo",
           "passportNumber")
OTHER_KEYS = ("dogumTarihi", "doğumTarihi", "DOĞUM TARİHİ", "dogum_yeri", "Doğum Yeri", "birthDate", "birth_place",
              "anaAdi", "anneAdi", "Anne Adı", "babaAdi", "Baba Adı", "BABA ADI", "motherName", "fatherName")
PARENT = "Sentetik Ebeveyn"
BIRTH = "1999-09-09T00:00:00"

# Labels of contact rows, in the spellings the type column may carry.
CONTACT_LABELS = ("Telefon", "Cep Telefonu", "CEP", "GSM", "Mobile", "İş Telefonu", "Ev Adresi", "ADRES", "İkamet",
                  "İKAMET ADRESİ", "ıkamet", "Address", "E-Posta", "E-POSTA", "e_posta", "eposta", "E-Mail", "Mail")


def cycle(values: tuple[str, ...], index: int) -> str:
    return values[index % len(values)]


def synthetic_payload() -> dict:
    personal: dict = {"adSoyad": "Deniz Örnek", "ogrenciNo": "990990990", "bolumAdiTR": "Örnek Mühendisliği"}
    for index, key in enumerate(EMAIL_KEYS):
        personal[key] = cycle(EMAILS, index)
    for index, key in enumerate(PHONE_KEYS):
        personal[key] = cycle(PHONES, index)
    for index, key in enumerate(ADDRESS_KEYS):
        personal[key] = cycle(ADDRESSES, index)
    for index, key in enumerate(ID_KEYS):
        personal[key] = cycle(NATIONAL_IDS, index)
    for index, key in enumerate(OTHER_KEYS):
        personal[key] = PARENT if "ad" in key.casefold() or "name" in key.casefold() else BIRTH
    # Secrets under field names that say nothing about them.
    personal["aciklama"] = "Ulaşmak için " + " veya ".join(EMAILS) + " yazın."
    personal["not"] = "Aranacak numaralar: " + "; ".join(PHONES)
    personal["serbestMetin"] = [f"Kimlik {NATIONAL_IDS[0]} ile kayıtlı.", {"ic": NATIONAL_IDS[1]}]
    personal["sayi"] = [5000000015, 10000000146, 905000000011]
    rows: list = []
    for index, label in enumerate(CONTACT_LABELS):
        secret = cycle(ADDRESSES, index) if "dres" in label or "kamet" in label.casefold() or "KAMET" in label \
            else cycle(SECRETS, index)
        key = "iletisimTipiAdiTR" if index % 2 == 0 else "iletisimTipiAdiEN"
        rows.append({key: label, "degeri": secret, "aciklama": cycle(ADDRESSES, index),
                     "ek": {"detay": cycle(ADDRESSES, index + 1), "liste": [cycle(PHONES, index)]},
                     "iletisimTipiId": index, "aktif": True})
    # Rows whose label says nothing, with the secret in the value or in another field.
    for index, secret in enumerate(EMAILS + PHONES + NATIONAL_IDS):
        rows.append({"iletisimTipiAdiTR": "Web", "degeri": secret})
        rows.append({"iletisimTipiAdiTR": "Diğer", "degeri": "x", "email": cycle(EMAILS, index),
                     "telefon": cycle(PHONES, index), "adres": cycle(ADDRESSES, index), "ek": f"bkz. {secret}"})
        rows.append(f"satır {secret}")
    return {
        "statusCode": 0,
        "kisiselBilgiler": personal,
        "iletisimBilgiList": [{"iletisimBilgiList": rows, "grupAdi": "İletişim", "not": f"grup {EMAILS[0]}"}],
        "redaction_note": "stale " + EMAILS[1],
        "redacted_fields": [PHONES[0]],
    }


class NothingLeaksTests(unittest.TestCase):
    def assert_clean(self, result: dict) -> None:
        rendered = json.dumps(result, ensure_ascii=False)
        for secret in SECRETS + (PARENT, BIRTH):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, rendered)
        # No bare e-mail address, 11 digit number or ten digit phone body is left at all.
        self.assertNotRegex(rendered, r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]")
        self.assertNotRegex(rendered, r"(?<![0-9])[0-9]{10,12}(?![0-9])")
        self.assertNotRegex(rendered, r"000[ .-]?00[ .-]?[0-9]{2}(?![0-9])")

    def test_no_secret_survives_in_the_rendered_output(self) -> None:
        self.assert_clean(redact_obs_profile(synthetic_payload()))

    def test_every_sensitive_spelling_is_hidden_by_its_name(self) -> None:
        personal = redact_obs_profile(synthetic_payload())["kisiselBilgiler"]
        for key in EMAIL_KEYS + PHONE_KEYS + ADDRESS_KEYS + ID_KEYS + OTHER_KEYS:
            with self.subTest(key=key):
                self.assertEqual(personal[key], REDACTED)

    def test_name_student_number_and_program_stay(self) -> None:
        result = redact_obs_profile(synthetic_payload())
        personal = result["kisiselBilgiler"]
        self.assertEqual(personal["adSoyad"], "Deniz Örnek")
        self.assertEqual(personal["ogrenciNo"], "990990990")
        self.assertEqual(personal["bolumAdiTR"], "Örnek Mühendisliği")
        self.assertEqual(result["statusCode"], 0)

    def test_contact_rows_keep_their_type_and_non_text_fields(self) -> None:
        rows = redact_obs_profile(synthetic_payload())["iletisimBilgiList"][0]["iletisimBilgiList"]
        for index, label in enumerate(CONTACT_LABELS):
            with self.subTest(label=label):
                row = rows[index]
                self.assertIn(label, (row.get("iletisimTipiAdiTR"), row.get("iletisimTipiAdiEN")))
                self.assertEqual(row["degeri"], REDACTED)
                self.assertEqual(row["aciklama"], REDACTED)
                self.assertEqual(row["ek"], {"detay": REDACTED, "liste": [REDACTED]})
                self.assertEqual(row["iletisimTipiId"], index)
                self.assertIs(row["aktif"], True)

    def test_the_report_describes_this_call_only(self) -> None:
        result = redact_obs_profile(synthetic_payload())
        self.assertNotIn(PHONES[0], result["redacted_fields"])
        self.assertIn("NINOVA_ALLOW_SENSITIVE_OUTPUT", result["redaction_note"])
        self.assertIn("kisiselBilgiler.E-Posta", result["redacted_fields"])
        self.assertIn("iletisimBilgiList: İkamet", result["redacted_fields"])
        self.assertEqual(len(result["redacted_fields"]), len(set(result["redacted_fields"])))

    def test_the_tools_do_not_leak_when_sensitive_output_is_off(self) -> None:
        for switch, include in (("0", False), ("0", True), ("1", False), ("", True)):
            for tool in ("obs_get_profile", "obs_get_contacts"):
                for raw in (False, True):
                    with self.subTest(switch=switch, include=include, tool=tool, raw=raw):
                        app = NinovaMcpApp()
                        app._client = MagicMock()
                        app._obs = MagicMock()
                        getattr(app._obs, tool.replace("obs_", "")).return_value = synthetic_payload()
                        with patch.dict("os.environ", {"NINOVA_ALLOW_SENSITIVE_OUTPUT": switch}):
                            self.assert_clean(getattr(app, tool)(include_sensitive=include, include_raw=raw))

    def test_the_operator_switch_and_the_argument_together_still_reveal(self) -> None:
        app = NinovaMcpApp()
        app._client = MagicMock()
        app._obs = MagicMock()
        app._obs.get_profile.return_value = synthetic_payload()
        with patch.dict("os.environ", {"NINOVA_ALLOW_SENSITIVE_OUTPUT": "1"}):
            rendered = json.dumps(app.obs_get_profile(include_sensitive=True, include_raw=True), ensure_ascii=False)
        for secret in (EMAILS[0], PHONES[0], NATIONAL_IDS[0], ADDRESSES[0]):
            self.assertIn(secret, rendered)


class TurkishFoldingTests(unittest.TestCase):
    def test_dotted_capital_i_in_a_contact_label(self) -> None:
        for label in ("İkamet", "İKAMET", "IKAMET", "ıkamet", "ikamet", "İkamet Adresi", "İş Telefonu", "E-POSTA ADRESİ"):
            with self.subTest(label=label):
                result = redact_obs_profile({"iletisimBilgiList": [{"iletisimTipiAdiTR": label, "degeri": ADDRESSES[0]}]})
                self.assertEqual(result["iletisimBilgiList"][0]["degeri"], REDACTED)
                self.assertEqual(result["redacted_fields"], [f"iletisimBilgiList: {label}"])

    def test_dotted_capital_i_in_a_field_name(self) -> None:
        for key in ("İkamet", "İKAMET", "IKAMET", "ADRESİ", "EMAİL", "KİMLİKNO", "KIMLIKNO", "DOĞUMTARİHİ", "TELEFON"):
            with self.subTest(key=key):
                self.assertEqual(redact_obs_profile({key: "değer"})[key], REDACTED)


class KeyNameTests(unittest.TestCase):
    def test_short_names_are_matched_as_whole_words_only(self) -> None:
        for key in ("hotel", "motel", "telif", "telafi", "model", "mailing", "cephe", "cepheNo", "tekel",
                    "intel", "pastel", "otomobil", "mobilya", "detail", "kartel"):
            with self.subTest(key=key):
                self.assertEqual(redact_obs_profile({key: "değer"}), {key: "değer", "redacted_fields": []})

    def test_short_names_are_matched_in_every_spelling(self) -> None:
        for key in ("tel", "Tel", "TEL", "tel_no", "Tel No", "tel-no", "telNo", "evTel", "ev_tel", "cep", "CEP",
                    "cepNo", "cep_no", "mail", "MAIL", "MAİL", "kisiselMail", "e-mail", "E Mail", "mobil", "Mobile"):
            with self.subTest(key=key):
                self.assertEqual(redact_obs_profile({key: "değer"})[key], REDACTED)


class ValuesWithoutDataTests(unittest.TestCase):
    def test_nothing_to_hide_is_not_hidden_and_not_listed(self) -> None:
        empty = {"gsm": 0, "telefon": 0.0, "mobil": False, "mail": None, "tel": "", "adres": "   ", "phone": [],
                 "email": {}, "ikamet": {"il": "", "ilce": None}, "cep": [None, "", 0]}
        self.assertEqual(redact_obs_profile(empty), {**empty, "redacted_fields": []})

    def test_values_that_carry_data_are_hidden(self) -> None:
        for value in ("x", 5, 2120000000, 0.5, True, [0, "x"], {"il": "Örnek"}):
            with self.subTest(value=value):
                result = redact_obs_profile({"telefon": value})
                self.assertEqual(result["telefon"], REDACTED)
                self.assertEqual(result["redacted_fields"], ["telefon"])

    def test_empty_contact_rows_are_not_listed(self) -> None:
        rows = [{"iletisimTipiAdiTR": "Cep", "degeri": 0, "aciklama": "", "ek": None, "sira": 0},
                {"iletisimTipiAdiTR": "Adres", "degeri": "  "}]
        result = redact_obs_profile({"iletisimBilgiList": rows})
        self.assertEqual(result, {"iletisimBilgiList": rows, "redacted_fields": []})


class StaleReportTests(unittest.TestCase):
    def test_an_incoming_note_is_dropped_when_nothing_was_hidden(self) -> None:
        result = redact_obs_profile({"adSoyad": "Deniz Örnek", "redaction_note": "old", "redacted_fields": ["old"]})
        self.assertEqual(result, {"adSoyad": "Deniz Örnek", "redacted_fields": []})

    def test_an_incoming_report_is_replaced_when_something_was_hidden(self) -> None:
        result = redact_obs_profile({"tel": "1", "redaction_note": "old", "redacted_fields": ["old"]})
        self.assertEqual(result["redacted_fields"], ["tel"])
        self.assertNotEqual(result["redaction_note"], "old")
        self.assertEqual(list(result)[-2:], ["redacted_fields", "redaction_note"])


class ValuePatternTests(unittest.TestCase):
    def masked(self, text: object) -> object:
        return redact_obs_profile({"alan": text})["alan"]

    def test_phone_numbers_are_masked_in_free_text(self) -> None:
        for phone in PHONES:
            with self.subTest(phone=phone):
                self.assertEqual(self.masked(f"Numara: {phone}, mesai içinde."), f"Numara: {REDACTED}, mesai içinde.")
                self.assertEqual(self.masked(phone), REDACTED)

    def test_national_ids_are_masked_in_free_text(self) -> None:
        for number in NATIONAL_IDS:
            with self.subTest(number=number):
                self.assertEqual(self.masked(f"TC {number} kayıtlı"), f"TC {REDACTED} kayıtlı")
                self.assertEqual(self.masked(int(number)), REDACTED)

    def test_numbers_that_are_a_phone_are_masked(self) -> None:
        for number in (5000000015, 2120000024, 905000000011):
            with self.subTest(number=number):
                self.assertEqual(self.masked(number), REDACTED)

    def test_look_alikes_are_left_alone(self) -> None:
        look_alikes = (
            "21345", "CRN 21345, 21346 ve 21347", "202610", "202610 2026-2027 Güz", "202510, 202520, 202530",
            "990990990", "150210099", "Öğrenci no: 150210099", "2026", "2025-2026", "2024 2025 2026",
            "3", "3.5", "4.00", "AKTS 7.5 kredi 3", "240 AKTS", "3.00 4.00 2.50 3.75",
            "2026-10-11", "2026-10-11T09:30:00", "11.10.2026", "11.10.2026 09:30", "10/11/2026", "08:30-11:29",
            "09:30 12:29", "BLG 212E", "MAT 103 21345", "D-301", "1999-09-09T00:00:00", "12345678", "1234567890123",
            "0123456789012", "TR000000000000000000000000", "v1.2.3", "100 / 100", "%87.5",
        )
        for text in look_alikes:
            with self.subTest(text=text):
                self.assertEqual(redact_obs_profile({"alan": text}), {"alan": text, "redacted_fields": []})
        for number in (21345, 202610, 990990990, 150210099, 2026, 3, 3.5, 900123, 1234567890123, 0, 1700000000):
            with self.subTest(number=number):
                self.assertEqual(redact_obs_profile({"alan": number}), {"alan": number, "redacted_fields": []})

    def test_a_record_of_course_data_is_untouched(self) -> None:
        record = {"ogrenciNo": "990990990", "crn": "21345", "donemKodu": "202610", "yil": 2026, "kredi": 3.5,
                  "akts": 7.5, "kayitTarihi": "2026-10-11T09:30:00", "sinifId": 900123, "dersKodu": "BLG 212E",
                  "saat": "08:30-11:29", "akademikDonemAdi": "2026-2027 Güz Dönemi"}
        before = deepcopy(record)
        self.assertEqual(redact_obs_profile(record), {**before, "redacted_fields": []})


if __name__ == "__main__":
    unittest.main()
