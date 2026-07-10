from __future__ import annotations

import unittest

from ninova_mcp.parsing import (
    compare_snapshot_payloads,
    extract_attendance,
    extract_announcements_list,
    extract_assignment_detail,
    extract_assignment_upload_status,
    extract_assignments_list,
    extract_course_sections,
    extract_gradebook,
    extract_course_info,
    extract_courses,
    extract_file_directory,
    extract_message_board,
    extract_message_thread_detail,
    extract_remote_learning,
    make_snapshot_payload,
    ninova_datetime_iso,
    normalize_url,
    parse_html_page,
    sanitize_filename,
)


SAMPLE_HTML = """
<html>
  <head><title>Test Ninova Page</title></head>
  <body>
    <h1>Hos Geldiniz</h1>
    <div>
      <span>BBF 201E</span>
      <a href="/Sinif/36851.118733">Olasilik ve Istatistik</a>
    </div>
    <table>
      <tr><th>Duyuru</th><th>Tarih</th></tr>
      <tr><td><a href="/Sinif/36851.118733/Duyuru/1">Quiz</a></td><td>20 Nisan</td></tr>
    </table>
    <a href="/files/sample.pdf">Sample PDF</a>
  </body>
</html>
"""

SAMPLE_DASHBOARD_HTML = """
<html>
  <body>
    <ul>
      <li>
        <span>BBF 201E</span>
        <a href="/Sinif/36851.118733"><span>CRN: 23980</span></a>
        <script>
          var body = '';
          body += '<span style="font-weight:bold;">Olasılık ve İstatistik</span><br style="clear:both;" />';
        </script>
      </li>
    </ul>
  </body>
</html>
"""

SAMPLE_ANNOUNCEMENTS_HTML = """
<html>
  <body>
    <div class="duyuruGoruntule">
      <h2><a href="/Sinif/36851.118733/Duyuru/611711">Quiz Reminder</a></h2>
      <div class="icerik">
        <span><strong>BBF 201E - Olasılık ve İstatistik</strong></span> /
        <span><a href="/Sinif/36851.118733" class="tarih">CRN: 23980</a></span><br />
        Quiz details are here.<br />
        <span class="tarih">13 Nisan 2026 14:49</span>
      </div>
      <div class="tarih"><span class="tarih">Behçet Uğur Töreyin</span></div>
    </div>
  </body>
</html>
"""

SAMPLE_ASSIGNMENTS_HTML = """
<html>
  <body>
    <table class="data" id="ctl00_ContentPlaceHolder1_gvOdevListesi">
      <tr><td>
        <h2><a href="/Sinif/36851.118733/Odev/250013">Homework 6</a></h2>
        <strong>Ders : </strong><a>BBF 201E - Olasılık ve İstatistik</a><br />
        <strong>Sınıf : </strong><a href="/Sinif/36851.118733">CRN: 23980</a><br />
        <strong>Teslim Başlangıcı : </strong>16 Nisan 2026 00:00<br />
        <strong>Teslim Bitişi : </strong>20 Nisan 2026 23:59<br />
        Ödevde istenen toplam <strong class="uyari">1</strong> adet dosyanın
        <strong class="uyari">0</strong> adedini sisteme yüklediniz.
        <a href="/Sinif/36851.118733/Odev/250013">Ödevi Görüntüle</a> |
        <a href="/Sinif/36851.118733/Odev/250013/OdevGonder">Ödevi Yükle</a>
      </td></tr>
    </table>
  </body>
</html>
"""

SAMPLE_ASSIGNMENT_DETAIL_HTML = """
<html>
  <body>
    <h1>Homework 6</h1>
    Teslim Başlangıcı 16 Nisan 2026 00:00
    Teslim Bitişi 20 Nisan 2026 23:59
    Ödev Açıklaması Solve all questions carefully.
    Kaynak Dosyalar
    <table>
      <tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>
      <tr><td><a href="/Sinif/36851.118733/Odev/250013?g1">Homework6.pdf</a></td><td>174 KB</td><td>16 Nisan 2026 00:35</td></tr>
    </table>
    İstenen Dosyalar
    <table>
      <tr><th>Açıklama</th><th>Uzantılar</th></tr>
      <tr><td>Solution file</td><td>*.pdf;</td></tr>
    </table>
    <a href="/Sinif/36851.118733/Odev/250013/OdevGonder">Ödevi Yükle</a>
    Yardım
  </body>
</html>
"""

SAMPLE_ASSIGNMENT_UPLOAD_HTML = """
<html>
  <body>
    <h1>Homework 6 Upload</h1>
    <table class="data" id="ctl00_ContentPlaceHolder1_gvOdevDosyaTipleri">
      <tr><th>Açıklama</th><th>Uzantılar</th></tr>
      <tr>
        <td>
          <strong>Solution file</strong><br />
          <a href="/files/homework6.pdf">homework6.pdf</a><br />
          Dosyayı gönderdiniz.
        </td>
        <td>*.pdf;</td>
      </tr>
      <tr>
        <td>
          <strong>Optional appendix</strong><br />
          <span class="uyari">Dosyayı henüz göndermediniz.</span>
        </td>
        <td>*.zip;</td>
      </tr>
    </table>
  </body>
</html>
"""

SAMPLE_ASSIGNMENTS_HTML_WITH_SPANS = """
<html>
  <body>
    <table class="data" id="ctl00_ContentPlaceHolder1_gvOdevListesi">
      <tr><td>
        <h2><a href="/Sinif/36851.118733/Odev/250013">Homework 6</a></h2>
        <strong>Ders : </strong><a>BBF 201E - Olasılık ve İstatistik</a><br />
        <strong>Sınıf : </strong><a href="/Sinif/36851.118733">CRN: 23980</a><br />
        <strong>Teslim Başlangıcı : </strong>16 Nisan 2026 00:00<br />
        <strong>Teslim Bitişi : </strong>20 Nisan 2026 23:59<br />
        <span>Ödevde istenen toplam</span>
        <strong class="uyari">2</strong>
        <span>dosyadan</span>
        <strong class="uyari">1</strong>
        <span>tanesini yüklediniz.</span>
      </td></tr>
    </table>
  </body>
</html>
"""

SAMPLE_FILE_DIRECTORY_HTML = """
<html>
  <body>
    <table>
      <tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>
      <tr>
        <td><img src="/images/ds/folder.png" /><a href="/Sinif/36851.118733/SinifDosyalari?g1">Recitations</a></td>
        <td>228 KB</td>
        <td>03 Mart 2026 15:11</td>
      </tr>
      <tr>
        <td><img src="/images/ds/ikon-pdf.png" /><a href="/Sinif/36851.118733/SinifDosyalari?g2">Syllabus.pdf</a></td>
        <td>214 KB</td>
        <td>20 Şubat 2026 14:05</td>
      </tr>
    </table>
  </body>
</html>
"""

SAMPLE_COURSE_INFO_HTML = """
<html>
  <body>
    <h1>Sınıf Bilgileri</h1>
    <table>
      <tr><td>Ders Kodu</td><td>BBF 201E</td></tr>
      <tr><td>Ders Adı</td><td>Türkçe</td><td>Olasılık ve İstatistik</td></tr>
      <tr><td>İngilizce</td><td>Probability and Statistics</td></tr>
      <tr><td>Sınıf Adı</td><td>CRN: 23980</td></tr>
    </table>
    <table>
      <tr><td>Dönem</td><td>2025-2026 Bahar Dönemi</td></tr>
      <tr><td>Başlangıç Tarihi</td><td>09 Şubat 2026</td></tr>
    </table>
    <table>
      <tr><td>Çarşamba 08:30 - 10:29</td><td>104</td></tr>
    </table>
    <table>
      <tr><td>Dersin Dili</td><td>İngilizce</td></tr>
      <tr><td>Dersin Koordinatörü</td><td>Behçet Uğur Töreyin</td></tr>
    </table>
    <table>
      <tr><th>Hafta</th><th>Konu</th></tr>
      <tr><td>1</td><td>Introduction</td></tr>
    </table>
  </body>
</html>
"""

SAMPLE_COURSE_HOME_HTML = """
<html>
  <body>
    <a href="/Sinif/36851.118733/SinifBilgileri">Sınıf Bilgileri</a>
    <a href="/Sinif/36851.118733/Notlar">Notlar</a>
    <a href="/Sinif/36851.118733/MesajPanosu">Mesaj Panosu</a>
    <a href="/Sinif/36851.118733/Yoklama">Yoklama</a>
  </body>
</html>
"""

SAMPLE_GRADES_HTML = """
<html>
  <body>
    <table>
      <tr><th>Ayşe Yılmaz</th><th>Not</th><th>Açıklama</th></tr>
      <tr><td>Midterm 1</td><td>95,00</td><td></td></tr>
      <tr><td>Homework 1</td><td>-</td><td>Pending</td></tr>
      <tr><td>Ağırlıklı Ortalamanız</td><td>47,50</td><td></td></tr>
    </table>
  </body>
</html>
"""

SAMPLE_MESSAGE_BOARD_HTML = """
<html>
  <body>
    <table>
      <tr><th>Mesaj Başlığı</th><th>Son Mesaj</th></tr>
      <tr>
        <td><a href="/Sinif/36851.118733/MesajPanosu/144429">Homework 4</a> Hi All, re-uploaded.</td>
        <td>Behçet Uğur Töreyin 29 Mart 2026 22:00</td>
      </tr>
    </table>
  </body>
</html>
"""

SAMPLE_MESSAGE_THREAD_HTML = """
<html>
  <body>
    <h1>Homework 4</h1>
    <table>
      <tr><th>Gönderen</th><th>Mesaj</th></tr>
      <tr><td>Behçet Uğur Töreyin 29 Mart 2026 22:00</td><td>Hi All, I re-uploaded the HW4 file.</td></tr>
      <tr><td>Mesaj</td><td>Cevapla Temizle</td></tr>
    </table>
  </body>
</html>
"""

SAMPLE_ATTENDANCE_HTML = """
<html>
  <body>
    <table>
      <tr><th>Öğrenci</th></tr>
      <tr><td>Ayşe Yılmaz</td></tr>
    </table>
    <table>
      <tr><th>Hafta</th><th>Çarşamba</th><th>Cuma</th></tr>
      <tr><td>1. Hafta</td><td>1 1</td><td>0 1</td></tr>
      <tr><td>2. Hafta</td><td>0 0</td><td>1 1</td></tr>
    </table>
  </body>
</html>
"""

SAMPLE_REMOTE_HTML = """
<html>
  <body>
    <h2>Aktif Uzaktan Eğitim Oturumlarınız</h2>
    <table>
      <tr><th>Başlık</th><th>Bağlantı</th></tr>
      <tr><td>Canlı Ders</td><td><a href="/join/1">Katıl</a></td></tr>
    </table>
    <h2>Sınıfın Geçmiş Uzaktan Eğitim Oturumları</h2>
    <table>
      <tr><td>Sınıfınıza eklenmiş herhangi bir uzaktan eğitim oturumu bulunmamaktadır.</td></tr>
    </table>
  </body>
</html>
"""


class ParsingTests(unittest.TestCase):
    def test_normalize_url(self) -> None:
        self.assertEqual(
            normalize_url("/Kampus1", "https://ninova.itu.edu.tr"),
            "https://ninova.itu.edu.tr/Kampus1",
        )

    def test_parse_html_page(self) -> None:
        page = parse_html_page(
            "https://ninova.itu.edu.tr/Kampus1",
            SAMPLE_HTML,
            base_url="https://ninova.itu.edu.tr",
        )
        self.assertEqual(page["title"], "Test Ninova Page")
        self.assertEqual(page["headings"][0]["text"], "Hos Geldiniz")
        self.assertEqual(len(page["attachments"]), 1)
        self.assertEqual(page["attachments"][0]["kind"], "file")

    def test_extract_courses(self) -> None:
        courses = extract_courses(
            SAMPLE_DASHBOARD_HTML,
            "https://ninova.itu.edu.tr/Kampus1",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0]["code"], "BBF 201E")
        self.assertEqual(courses[0]["title"], "Olasılık ve İstatistik")

    def test_compare_snapshots(self) -> None:
        previous_page = parse_html_page(
            "https://ninova.itu.edu.tr/Kampus1",
            SAMPLE_HTML,
            base_url="https://ninova.itu.edu.tr",
        )
        current_page = parse_html_page(
            "https://ninova.itu.edu.tr/Kampus1",
            SAMPLE_HTML.replace("Quiz", "Updated Quiz"),
            base_url="https://ninova.itu.edu.tr",
        )
        diff = compare_snapshot_payloads(
            make_snapshot_payload(previous_page),
            make_snapshot_payload(current_page),
        )
        self.assertTrue(diff["text_changed"])
        self.assertTrue(diff["text_diff_preview"])

    def test_extract_announcements_list(self) -> None:
        items = extract_announcements_list(
            SAMPLE_ANNOUNCEMENTS_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/Duyurular",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Quiz Reminder")
        self.assertEqual(items[0]["course_title"], "BBF 201E - Olasılık ve İstatistik")
        self.assertEqual(items[0]["author"], "Behçet Uğur Töreyin")

    def test_extract_assignments(self) -> None:
        items = extract_assignments_list(
            SAMPLE_ASSIGNMENTS_HTML,
            "https://ninova.itu.edu.tr/Kampus?1/Odevler",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Homework 6")
        self.assertEqual(items[0]["requested_file_count"], 1)
        self.assertEqual(items[0]["uploaded_file_count"], 0)
        self.assertEqual(items[0]["class_name"], "CRN: 23980")

        detail = extract_assignment_detail(
            SAMPLE_ASSIGNMENT_DETAIL_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/Odev/250013",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(detail["title"], "Homework 6")
        self.assertEqual(detail["source_files"][0]["name"], "Homework6.pdf")
        self.assertEqual(detail["required_files"][0]["extensions"], "*.pdf;")

    def test_extract_assignments_with_dom_fallback_and_upload_status(self) -> None:
        items = extract_assignments_list(
            SAMPLE_ASSIGNMENTS_HTML_WITH_SPANS,
            "https://ninova.itu.edu.tr/Kampus?1/Odevler",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(items[0]["requested_file_count"], 2)
        self.assertEqual(items[0]["uploaded_file_count"], 1)

        upload_status = extract_assignment_upload_status(
            SAMPLE_ASSIGNMENT_UPLOAD_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/Odev/250013/OdevGonder",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(upload_status["requested_file_count"], 2)
        self.assertEqual(upload_status["uploaded_file_count"], 1)
        self.assertTrue(upload_status["upload_items"][0]["uploaded"])
        self.assertFalse(upload_status["upload_items"][1]["uploaded"])
        self.assertEqual(
            upload_status["upload_items"][0]["file_url"],
            "https://ninova.itu.edu.tr/files/homework6.pdf",
        )

    def test_extract_file_directory_and_course_info(self) -> None:
        listing = extract_file_directory(
            SAMPLE_FILE_DIRECTORY_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/SinifDosyalari",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(len(listing["entries"]), 2)
        self.assertEqual(listing["entries"][0]["entry_type"], "folder")
        self.assertEqual(listing["entries"][1]["entry_type"], "file")

        info = extract_course_info(
            SAMPLE_COURSE_INFO_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/SinifBilgileri",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(info["identity"]["Ders Kodu"], "BBF 201E")
        self.assertEqual(info["class_meta"]["Dönem"], "2025-2026 Bahar Dönemi")
        self.assertEqual(info["weekly_schedule"][0]["location"], "104")

    def test_extract_sections_grades_messages_attendance_and_remote(self) -> None:
        sections = extract_course_sections(
            SAMPLE_COURSE_HOME_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(sections[0]["path"], "SinifBilgileri")

        grades = extract_gradebook(
            SAMPLE_GRADES_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/Notlar",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(grades["student_name"], "Ayşe Yılmaz")
        self.assertEqual(grades["weighted_average"], "47,50")
        self.assertEqual(grades["grades"][0]["score"], "95,00")

        message_board = extract_message_board(
            SAMPLE_MESSAGE_BOARD_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/MesajPanosu",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(message_board["topics"][0]["title"], "Homework 4")
        self.assertEqual(message_board["topics"][0]["last_message_at_iso"], "2026-03-29T22:00:00+03:00")

        thread = extract_message_thread_detail(
            SAMPLE_MESSAGE_THREAD_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/MesajPanosu/144429",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(thread["count"], 1)
        self.assertEqual(thread["posts"][0]["author"], "Behçet Uğur Töreyin")

        attendance = extract_attendance(
            SAMPLE_ATTENDANCE_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/Yoklama",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(attendance["student_name"], "Ayşe Yılmaz")
        self.assertEqual(attendance["total_present_marks"], 5)
        self.assertEqual(attendance["total_absent_marks"], 3)

        remote = extract_remote_learning(
            SAMPLE_REMOTE_HTML,
            "https://ninova.itu.edu.tr/Sinif/36851.118733/UzaktanEgitim",
            "https://ninova.itu.edu.tr",
        )
        self.assertEqual(remote["active_count"], 1)
        self.assertEqual(remote["active_sessions"][0]["Başlık"], "Canlı Ders")

    def test_datetime_and_filename_helpers(self) -> None:
        self.assertEqual(ninova_datetime_iso("29 Mart 2026 22:00"), "2026-03-29T22:00:00+03:00")
        self.assertEqual(sanitize_filename("Çalışma Kağıdı 1.pdf"), "Çalışma Kağıdı 1.pdf")
        self.assertEqual(sanitize_filename("folder/name?.pdf"), "folder-name-.pdf")


if __name__ == "__main__":
    unittest.main()
