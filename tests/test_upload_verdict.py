"""What ``submit_assignment`` reports after the POST: the verdict is about the chosen slot.

Synthetic pages only. Nothing leaves the process: the upload page and the
answer to the POST come from the offline recorder.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from ninova_mcp.assignment_upload import (
    CHECK_NINOVA,
    LOGIN_PAGE_AFTER_POST,
    file_names_match,
    judge_upload,
    page_error_message,
    page_success_phrase,
    page_texts,
    slot_upload_dates,
)
from ninova_mcp.client import NinovaError
from ninova_mcp.parsing import extract_assignment_upload_form

from test_submit_assignment_phases import BASE, FORM_ACTION, SUBMIT_TARGET, UPLOAD_URL, UploadFlowCase

EMPTY_STATUS = "Dosyayı henüz göndermediniz."
OLD_DATE = "24 Haziran 2026 13:35"
NEW_DATE = "10 Temmuz 2026 12:00"
FIRST_SLOT = "slot 1 (Table of Contents (Internship Report Approval Form))"
ABROAD_SLOT = "slot 5 (Abroad Petition and Other Documents (Passport, Insurance,...etc))"


def slot(**values: Any) -> dict[str, Any]:
    return {
        "index": 2, "description": "Rapor", "field_name": "f2", "uploaded": False,
        "status_text": "Rapor " + EMPTY_STATUS, "file_name": None, **values,
    }


def page_of(*slots: dict[str, Any], ok: bool = True, error: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "error": error, "slots": list(slots)}


def uploaded_slot(date: str = NEW_DATE, **values: Any) -> dict[str, Any]:
    return slot(uploaded=True, status_text=f"Rapor Dosyayı {date} tarihinde sisteme yüklediniz.", **values)


class SubmitOutcomeTests(UploadFlowCase):
    """The whole tool, on the upload page fixture and an answer page built from it."""

    def after_page(self, old: str, new: str) -> str:
        changed = self.html.replace(old, new)
        self.assertNotEqual(changed, self.html)
        return changed

    def test_slot_that_holds_the_file_after_the_post_is_uploaded(self) -> None:
        app, _ = self.app(after_html=self.after_page("henüz göndermediniz", f"{NEW_DATE} tarihinde sisteme yüklediniz"))
        result = self.submit(app)
        self.assertEqual((result["ok"], result["status"]), (True, "uploaded"))
        self.assertEqual(result["message"], (
            f"Upload confirmed for {ABROAD_SLOT}: it was empty before and holds a file now, upload date {NEW_DATE}."
        ))
        self.assertIs(result["slot_after"]["uploaded"], True)

    def test_empty_slot_next_to_slots_that_say_uploaded_is_not_confirmed(self) -> None:
        app, _ = self.app()
        result = self.submit(app)
        self.assertIn("yüklediniz", self.html)
        self.assertEqual((result["ok"], result["status"]), (False, "not_confirmed"))
        self.assertIs(result["slot_after"]["uploaded"], False)
        self.assertTrue(result["message"].startswith("Upload NOT confirmed. The POST completed, but "))
        self.assertIn(f"{ABROAD_SLOT} still shows no file", result["message"])
        self.assertTrue(result["message"].endswith(CHECK_NINOVA))
        self.assertNotIn("confirmed for", result["message"])

    def test_error_text_with_the_slot_still_empty_is_failed(self) -> None:
        error = '<span id="lblHata" class="uyari">Dosya uzantısı geçersiz: yalnızca *.pdf yüklenebilir.</span>'
        app, _ = self.app(after_html=self.after_page("<h2>Gönderilecek Dosyalar</h2>", f"<h2>Gönderilecek Dosyalar</h2>{error}"))
        result = self.submit(app)
        self.assertEqual((result["ok"], result["status"]), (False, "failed"))
        self.assertEqual(result["message"], (
            "Upload failed. Ninova's answer page reports: 'Dosya uzantısı geçersiz: yalnızca *.pdf yüklenebilir.'. "
            f"Also, {ABROAD_SLOT} still shows no file ({result['slot_after']['status_text']!r})."
        ))
        self.assertIs(result["slot_after"]["uploaded"], False)

    def test_text_the_upload_page_already_showed_is_not_an_error_of_the_post(self) -> None:
        # The help box of the page warns about rejected files before anything is posted.
        warned = self.html.replace("<h2>Yardım</h2>", "<h2>Yardım</h2><p>Geçersiz uzantılar hata verir.</p>")
        app, _ = self.app(warned)
        result = self.submit(app)
        self.assertEqual(result["status"], "not_confirmed")

    def test_login_page_after_the_post_is_failed(self) -> None:
        app, _ = self.app()
        post = self.posted_page(app, '<html><body><form><input id="ContentPlaceHolder1_tbUserName"></form></body></html>')
        self.posted_is_login_page = True
        result = self.direct(app)
        post.assert_called_once()
        self.assertEqual((result["ok"], result["status"]), (False, "failed"))
        self.assertEqual(result["message"], LOGIN_PAGE_AFTER_POST)
        self.assertIsNone(result["slot_after"]["uploaded"])

    def test_replacement_with_an_unchanged_date_is_not_confirmed(self) -> None:
        app, _ = self.app()
        result = self.submit(app, slot_index=1, allow_replace=True)
        self.assertIs(result["slot_before"]["already_uploaded"], True)
        self.assertIs(result["slot_after"]["uploaded"], True)
        self.assertEqual((result["ok"], result["status"]), (False, "not_confirmed"))
        self.assertEqual(result["message"], (
            f"Upload NOT confirmed. The POST completed, but {FIRST_SLOT} already held a file before the upload "
            f"and looks the same afterwards (upload date {OLD_DATE}, file name not shown), "
            f"so the new file cannot be told from the old one. {CHECK_NINOVA}"
        ))

    def test_replacement_with_a_new_date_is_uploaded(self) -> None:
        first = f'ctl02_lblGonderildi">Dosyayı <strong>{OLD_DATE}</strong>'
        app, _ = self.app(after_html=self.after_page(first, first.replace(OLD_DATE, NEW_DATE)))
        result = self.submit(app, slot_index=1, allow_replace=True)
        self.assertEqual((result["ok"], result["status"]), (True, "uploaded"))
        self.assertEqual(result["message"], (
            f"Upload confirmed for {FIRST_SLOT}: it held a file before and "
            f"its upload date is now {NEW_DATE} (was {OLD_DATE})."
        ))

    def test_a_new_date_in_another_slot_confirms_nothing(self) -> None:
        second = f'ctl03_lblGonderildi">Dosyayı <strong>{OLD_DATE}</strong>'
        app, _ = self.app(after_html=self.after_page(second, second.replace(OLD_DATE, NEW_DATE)))
        for arguments in ({"slot_index": 1, "allow_replace": True}, {"slot_index": 5}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.submit(app, **arguments)["status"], "not_confirmed")

    def test_answer_that_cannot_be_read_as_slots_is_not_confirmed(self) -> None:
        answers = {
            "<html><body><p>Bakım çalışması</p></body></html>": "No HTML form found on the upload page.",
            "<html><body><form><p>Tamam</p></form></body></html>": "it lists no upload slots",
            "": "No HTML form found on the upload page.",
        }
        for answer, why in answers.items():
            with self.subTest(answer=answer):
                app, _ = self.app()
                self.posted_page(app, answer)
                result = self.direct(app)
                self.assertEqual((result["ok"], result["status"]), (False, "not_confirmed"))
                self.assertEqual(result["message"], (
                    "Upload NOT confirmed. The POST completed, but the answer page could not be read as "
                    f"the upload page ({why}). {CHECK_NINOVA}"
                ))
                self.assertEqual(result["slot_after"], {
                    "index": 5, "description": self.form["slots"][4]["description"],
                    "uploaded": None, "file_name": None, "status_text": None,
                })

    def test_answer_that_no_longer_lists_the_slot_is_not_confirmed(self) -> None:
        start = self.html.index("<tr>", self.html.index("ctl05_fuUpload"))
        end = self.html.index("</tr>", start) + len("</tr>")
        app, _ = self.app(after_html=self.html[:start] + self.html[end:])
        result = self.submit(app)
        self.assertEqual(result["status"], "not_confirmed")
        self.assertIn(f"the answer page lists slots 1, 2, 3, 4 but not {ABROAD_SLOT}", result["message"])

    def test_answer_from_another_page_confirms_nothing(self) -> None:
        # Even slots that look uploaded prove nothing when they are not the upload page's own.
        uploaded = self.after_page("henüz göndermediniz", f"{NEW_DATE} tarihinde sisteme yüklediniz")
        for path in ("/Sinif/32945.119022/Odev/245598", "/Sinif/32945.119022/Odev/999999/OdevGonder", "/Kampus1"):
            with self.subTest(path=path):
                app, _ = self.app()
                self.posted_page(app, uploaded, url=BASE + path)
                result = self.direct(app)
                self.assertEqual((result["ok"], result["status"]), (False, "not_confirmed"))
                self.assertIs(result["slot_after"]["uploaded"], True)
                self.assertEqual(result["response_url"], BASE + path)
                self.assertIn(
                    f"the answer came from {BASE + path}, which is not the upload page of this assignment",
                    result["message"],
                )
        app, _ = self.app()
        self.posted_page(app, uploaded, url=UPLOAD_URL + "?a=1")
        self.assertEqual(self.direct(app)["status"], "uploaded")

    def test_the_tool_description_tells_a_model_how_to_relay_the_status(self) -> None:
        from ninova_mcp.tool_metadata import ninova

        description = next(tool["description"] for tool in ninova.NINOVA_TOOLS if tool["name"] == "submit_assignment")
        for needed in ("`uploaded`", "`not_confirmed`", "`failed`", "check Ninova manually", "never report it as submitted"):
            self.assertIn(needed, description)


class JudgeUploadTests(unittest.TestCase):
    def judge(self, before: dict[str, Any], *after: dict[str, Any], name: str = "rapor.pdf", **kwargs: Any) -> Any:
        page = kwargs.pop("page", None) or page_of(*after)
        chosen = next((item for item in page["slots"] if item["index"] == before["index"]), None)
        return judge_upload(before, page, chosen, file_name=name, **kwargs)

    def test_empty_slot_that_now_holds_a_file_is_uploaded(self) -> None:
        verdict = self.judge(slot(), uploaded_slot(file_name="rapor.pdf"))
        self.assertEqual(verdict.status, "uploaded")
        self.assertEqual(verdict.message, (
            f"Upload confirmed for slot 2 (Rapor): it was empty before and holds a file now, "
            f"upload date {NEW_DATE}; the file shown is 'rapor.pdf'."
        ))
        renamed = self.judge(slot(), uploaded_slot(file_name="baska.pdf"))
        self.assertEqual(renamed.status, "uploaded")
        self.assertIn("the file shown is 'baska.pdf', not the posted name 'rapor.pdf'", renamed.message)

    def test_uploaded_must_be_exactly_true(self) -> None:
        for value in (False, None, 0, "", "yes", 1):
            with self.subTest(value=value):
                self.assertEqual(self.judge(slot(), slot(uploaded=value)).status, "not_confirmed")

    def test_replacement_needs_a_changed_date_or_name(self) -> None:
        before = uploaded_slot(OLD_DATE, file_name="rapor.pdf")
        cases = {
            "same": (uploaded_slot(OLD_DATE, file_name="rapor.pdf"), "not_confirmed"),
            "same name, other case": (uploaded_slot(OLD_DATE, file_name="RAPOR.PDF"), "not_confirmed"),
            "new date": (uploaded_slot(NEW_DATE, file_name="rapor.pdf"), "uploaded"),
            "new name": (uploaded_slot(OLD_DATE, file_name="rapor_v2.pdf"), "uploaded"),
            "name gone": (uploaded_slot(OLD_DATE), "not_confirmed"),
            "date gone": (slot(uploaded=True, status_text="Rapor yüklediniz", file_name="rapor.pdf"), "not_confirmed"),
            "emptied": (slot(), "not_confirmed"),
        }
        for label, (after, expected) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.judge(before, after).status, expected)
        self.assertIn("its file is now 'rapor_v2.pdf' (was 'rapor.pdf')", self.judge(before, cases["new name"][0]).message)

    def test_a_date_that_appears_where_none_was_shown_counts_as_new(self) -> None:
        before = slot(uploaded=True, status_text="Rapor yüklediniz")
        verdict = self.judge(before, uploaded_slot(NEW_DATE))
        self.assertEqual(verdict.status, "uploaded")
        self.assertIn(f"its upload date is now {NEW_DATE}.", verdict.message)

    def test_slot_at_the_same_position_with_another_file_field_proves_nothing(self) -> None:
        verdict = self.judge(slot(), uploaded_slot(field_name="f9"))
        self.assertEqual(verdict.status, "not_confirmed")
        self.assertIn("slot 2 of the answer page is not the slot the file was posted to", verdict.message)
        # A slot whose file field is gone after the upload is still the same row.
        self.assertEqual(self.judge(slot(), uploaded_slot(field_name=None)).status, "uploaded")

    def test_missing_slot_and_unreadable_page_are_not_confirmed(self) -> None:
        other = uploaded_slot(index=1, field_name="f1")
        self.assertIn("lists slots 1 but not slot 2 (Rapor)", self.judge(slot(), other).message)
        unreadable = self.judge(slot(), page=page_of(ok=False, error="No HTML form found on the upload page."))
        self.assertEqual(unreadable.status, "not_confirmed")
        self.assertIn("(No HTML form found on the upload page.)", unreadable.message)

    def test_an_error_message_fails_only_what_the_slot_does_not_prove(self) -> None:
        failed = self.judge(slot(), slot(), error_message="Bir hata oluştu")
        self.assertEqual(failed.status, "failed")
        self.assertTrue(failed.message.startswith("Upload failed. Ninova's answer page reports: 'Bir hata oluştu'."))
        self.assertEqual(self.judge(slot(), page=page_of(), error_message="Hata").status, "failed")
        unchanged = uploaded_slot(OLD_DATE)
        self.assertEqual(self.judge(unchanged, unchanged, error_message="Hata").status, "failed")
        self.assertEqual(self.judge(slot(), uploaded_slot(), error_message="Hata").status, "uploaded")

    def test_a_success_phrase_is_quoted_and_never_decides(self) -> None:
        for after in ((slot(),), (uploaded_slot(index=1, field_name="f1"),), ()):
            with self.subTest(after=after):
                verdict = self.judge(slot(), *after, success_phrase="Dosyanız başarıyla yüklendi")
                self.assertEqual(verdict.status, "not_confirmed")
                self.assertIn(
                    "The page does say 'Dosyanız başarıyla yüklendi', but that is not tied to this slot "
                    "and is not taken as confirmation.", verdict.message,
                )
        confirmed = self.judge(slot(), uploaded_slot(), success_phrase="Dosyanız başarıyla yüklendi")
        self.assertNotIn("başarıyla", confirmed.message)


class UploadEvidenceHelperTests(unittest.TestCase):
    def test_upload_dates_of_a_status_line(self) -> None:
        cases = {
            f"Rapor Dosyayı {OLD_DATE} tarihinde sisteme yüklediniz.": (OLD_DATE,),
            "Dosyayı 3 Şubat 09:05 tarihinde yüklediniz": ("3 Şubat 09:05",),
            "Yüklendi: 24.06.2026 13:35:10": ("24.06.2026 13:35:10",),
            "Rapor 2 Dosyayı henüz göndermediniz.": (),
            "Odev 3 teslim 12:00 Dosyayı henüz göndermediniz.": (),
            "": (),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slot_upload_dates({"status_text": text}), expected)
        self.assertEqual(slot_upload_dates(None), ())
        self.assertEqual(slot_upload_dates({}), ())

    def test_file_names_are_compared_normalized(self) -> None:
        same = [
            ("rapor.pdf", "rapor.pdf"), ("Rapor Son.PDF", "rapor_son.pdf"), ("ödev.zip", "ODEV.zip"),
            ("rapor.pdf", "rapor_150200001.pdf"), ("rapor.pdf", "150200001-rapor.pdf"),
        ]
        different = [
            ("rapor.pdf", "rapor.zip"), ("rapor.pdf", "sunum.pdf"), ("rapor.pdf", None), (None, "rapor.pdf"),
            ("a.pdf", "data.pdf"), ("rapor.pdf", ""), ("", ""),
        ]
        for posted, shown in same:
            with self.subTest(posted=posted, shown=shown):
                self.assertTrue(file_names_match(posted, shown))
        for posted, shown in different:
            with self.subTest(posted=posted, shown=shown):
                self.assertFalse(file_names_match(posted, shown))

    def test_only_text_the_post_added_is_a_message(self) -> None:
        before_html = "<p>Geçersiz uzantılar reddedilir, hata alırsınız.</p><td>Dosyayı <strong>1 Mart 2026 10:00</strong> yüklediniz.</td>"
        before = page_texts(before_html)
        self.assertIsNone(page_error_message(before_html, before))
        self.assertIsNone(page_success_phrase(before_html, before))
        after_html = before_html + '<div><span class="uyari">Dosya <b>yüklenemedi</b>.</span></div><p>İşlem başarıyla tamamlandı</p>'
        self.assertEqual(page_error_message(after_html, before), "Dosya yüklenemedi .")
        self.assertEqual(page_success_phrase(after_html, before), "İşlem başarıyla tamamlandı")
        self.assertEqual(page_error_message(after_html), "Geçersiz uzantılar reddedilir, hata alırsınız.")
        for harmless in ("<p>Tamam</p>", "<script>var error = 'failed';</script>", "<p>Hatay ili</p>", ""):
            with self.subTest(html=harmless):
                self.assertIsNone(page_error_message(harmless))
        self.assertEqual(len(page_error_message("<p>" + "hata " * 200 + "</p>") or ""), 200)


class UploadPageErrorTests(UploadFlowCase):
    """Every way the parser fails names its own problem, and that is what the tool says."""

    SUBMIT_LINK = f"<a href=\"javascript:__doPostBack('{SUBMIT_TARGET}','')\">Ödevi Gönder</a>"
    NO_FORM = "No HTML form found on the upload page."
    NO_SUBMIT = "Could not find the assignment submit control (Ödevi Gönder)."

    def pages(self) -> dict[str, tuple[str, str]]:
        return {
            "empty document": ("", self.NO_FORM),
            "no form": ("<html><body><p>Bakım</p></body></html>", self.NO_FORM),
            "empty form": (f"<form {FORM_ACTION}></form>", self.NO_SUBMIT),
            "form with a file field only": (f'<form {FORM_ACTION}><input type="file" name="f"></form>', self.NO_SUBMIT),
            "only the language switch posts back": (
                f"<form {FORM_ACTION}><a href=\"javascript:__doPostBack('ctl00$Header1$lbDil','')\">English</a></form>",
                self.NO_SUBMIT,
            ),
            "submit button without a name": (
                f'<form {FORM_ACTION}><input type="submit" value="Gönder"><button>Gönder</button></form>', self.NO_SUBMIT,
            ),
            "submit button with an empty name": (
                f'<form {FORM_ACTION}><input type="submit" name="" value="Gönder"></form>', self.NO_SUBMIT,
            ),
            "the fixture without its submit link": (self.html.replace("lbEkle", "lbBaska").replace("Ödevi Gönder", "Devam"), None),
        }

    def test_each_parser_error_reaches_the_caller_as_its_own_message(self) -> None:
        for label, (html, message) in self.pages().items():
            with self.subTest(page=label):
                form = extract_assignment_upload_form(html, UPLOAD_URL, BASE)
                if message is None:
                    # A page the parser accepts: any content postback stands in for the submit link.
                    self.assertTrue(form["ok"] and form["submit_event_target"])
                    self.assertIsNone(form["error"])
                    continue
                self.assertEqual(form["error"], message)
                self.events.clear()
                app, _ = self.app()
                with patch.object(app.client, "get_html", return_value=(html, type("R", (), {"url": UPLOAD_URL})())):
                    with self.assertRaises(NinovaError) as caught:
                        app._open_upload_page(UPLOAD_URL)
                    self.assertEqual(str(caught.exception), message)
                    with self.assertRaises(NinovaError) as caught:
                        self.submit(app)
                    self.assertEqual(str(caught.exception), message)
                self.assertEqual(self.events, [])

    def test_the_parser_never_fails_without_saying_why(self) -> None:
        for label, (html, _) in self.pages().items():
            with self.subTest(page=label):
                form = extract_assignment_upload_form(html, UPLOAD_URL, BASE)
                if not form.get("ok") or not form.get("submit_event_target"):
                    self.assertIsInstance(form["error"], str)
                    self.assertTrue(form["error"])
                else:
                    self.assertIsNone(form["error"])


class BlankSlotDescriptionTests(unittest.TestCase):
    def test_a_blank_description_matches_no_slot_even_one_without_a_description(self) -> None:
        from ninova_mcp.parsing import match_upload_slot

        slots = [{"index": 1, "description": ""}, {"index": 2, "description": None}, {"index": 3, "description": "Rapor"}]
        for blank in ("   ", "	", " "):
            for candidates in (slots, slots[:1]):
                with self.subTest(blank=blank, count=len(candidates)):
                    with self.assertRaises(ValueError) as caught:
                        match_upload_slot(candidates, slot_description=blank)
                    self.assertTrue(str(caught.exception).startswith(f"No slot matching {blank!r}. Available: "))


if __name__ == "__main__":
    unittest.main()
