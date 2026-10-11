"""Synthetic pages and the case list for the parsing characterization tests.

Every page here is invented: course codes, names, CRNs and dates are not
real. Each page exists to reach one branch of a parser in ``ninova_mcp.parsing``
(a missing table, a short row, an extra column, a placeholder cell ...). The
expected outputs live in ``fixtures/parsing_characterization.json`` and were
recorded from the parsers before they were split into modules.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from ninova_mcp import parsing

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_PATH = FIXTURES / "parsing_characterization.json"
BASE = "https://ninova.itu.edu.tr"
PAGE = BASE + "/Sinif/1234.5678/Sayfa"
TODAY = date(2030, 10, 11)
TERM_START = date(2030, 9, 2)
# Larger outputs are pinned by digest so the recorded file stays reviewable.
FULL_OUTPUT_LIMIT = 3000
# Fixtures above this size are parsed as they are but not mutated.
MUTATION_SIZE_LIMIT = 16000
# Parsers that return a sizeable structure for any page at all are recorded in
# full only on the pages written for them, by digest everywhere else.
FULL_OUTPUT_PAGES: dict[str, tuple[str, ...]] = {
    "parse_html_page": ("generic_page", "long_link_context", "unclosed"),
    "make_snapshot_payload": ("generic_page",),
    "summarize_dashboard": ("dashboard", "ninova_dashboard"),
    "extract_prerequisite_list": ("prerequisites", "onsart"),
    "extract_course_info": ("course_info",),
    "extract_announcement_detail": ("announcement_detail",),
    "extract_assignment_detail": ("assignment_detail",),
    "extract_message_thread_detail": ("message_thread",),
    "extract_assignment_upload_form": ("upload", "odev_gonder"),
}
SKIPPED_FIXTURES = {"akademik_takvim.html"}  # 330 KB calendar page, parsed elsewhere

SYNTHETIC: dict[str, str] = {
    "empty": "",
    "whitespace": "  \n ",
    "bare_html": "<html><head><title>Bos</title></head><body></body></html>",
    "text_only": "<p>Sadece metin, tablo yok. Yardım</p>",
    "unclosed": "<table><tr><td>a<td>b<tr><th>c",
    # ---------------------------------------------------------------- generic
    "generic_page": """
        <html><head><title>  Genel   Sayfa </title><style>.x{}</style><script>var a = 1;</script></head><body>
        <h1>Baslik Bir</h1><h3>Alt Baslik</h3><h2></h2>
        <p>Metin <a href=" /Sinif/1.2/Dosya 1.pdf ">Dosya</a> <a href="javascript:void(0)">js</a>
        <a href="">bos</a> <a href="https://example.org/x" title="Dis baglanti"></a>
        <a href="mailto:kisi@example.org">posta</a> <a href="/Sinif/1.2">Ders</a>
        <a href="/Sinif/1.2">Ders</a> <a href="relative/page">Goreli</a></p>
        <table><caption> Tablo Basligi </caption>
          <tr><th>Ad</th><th>Aralik</th></tr>
          <tr><td>Bir</td><td>25 Şubat - 31 Aralık 00:00 18:00</td></tr>
          <tr><td>Iki</td><td>01 Mart 2031 09:00 - 02 Mart 2031 10:00</td><td>fazla</td></tr>
          <tr></tr>
        </table>
        <table><tr><td>basliksiz</td><td>01 Ocak - 02 Ocak</td></tr><tr><td>tek</td></tr></table>
        <table><tr><th>Dis</th></tr><tr><td><table><tr><td>ic 1</td></tr><tr><td>ic 2</td></tr></table></td></tr></table>
        <table><tr><th>date_ranges</th><th>X</th></tr><tr><td>01 Ocak - 02 Ocak</td><td>y</td></tr></table>
        <noscript>gizli</noscript>
        </body></html>""",
    "long_link_context": "<p>" + ("uzun metin " * 40) + '<a href="/a">bag</a></p>',
    # -------------------------------------------------------------- dashboard
    "dashboard_codes": """
        <html><body><h2>Derslerim</h2>
        <ul><li><span>ZZT 101E</span><ul><li>
            <a href="/Sinif/100.1"><span>CRN: 12345</span></a>
            <script>body += '<span style="font-weight:bold;">Sentetik Giris</span>';</script>
        </li></ul></li>
        <li><span>Kod yok</span><ul><li><a href="/Sinif/200.1">QQQ 202 Sube A</a></li></ul></li>
        <li><ul><li><a href="/Sinif/300.1">ZZT 2030 Yaz Staji</a></li></ul></li>
        <li><ul><li><a href="/Sinif/400.1"></a><script>var x = 1;</script></li></ul></li>
        <li><span>ZZT 500</span><a href="/Sinif/500.1">Bir</a><a href="/Sinif/500.2">Iki</a><a href="/Sinif/500.1">Tekrar</a></li>
        </ul>
        <a href="https://example.org/Sinif/1.1">Dis</a><a href="/Sinif/1.1/Duyurular">Kok degil</a>
        <h2>Son Duyurular</h2>
        <table><tr><td>Vize sonuclari</td><td>07 Ekim 15:20</td></tr><tr><td>uc</td><td>hucre</td><td>satir</td></tr>
        <tr><td>Gecersiz</td><td>31 Şubat 10:00</td></tr></table>
        <h2>Son Ödevler</h2>
        <table><tr><th>Ödev</th><th>Tarih</th></tr><tr><td>Odev 1</td><td>12 Kasım 23:59</td></tr>
        <tr><td>Odev 2</td><td>25 Şubat - 31 Aralık 00:00 18:00</td></tr><tr><td>Odev 3</td><td>01 Ocak 2031 10:00</td></tr></table>
        <h2>Son Mesajlar</h2>
        </body></html>""",
    "dashboard_no_tables": "<html><body><h2>Son Duyurular</h2><h2>Son Ödevler</h2><p>yok</p></body></html>",
    "dashboard_deep_anchor": """
        <div><span>ZZT 777</span><div><div><div><div><div>
        <b><i><u><a href="/Sinif/700.1">Derin</a></u></i></b></div></div></div></div></div></div>""",
    "dashboard_lone_anchor": '<a href="/Sinif/800.1">Tek</a>',
    "dashboard_year_like_only": '<div><a href="/Sinif/300.1">ZZT 2030 Yaz Staji</a></div>',
    # ---------------------------------------------------------- announcements
    "announcements_cards": """
        <div class="duyuruGoruntule"><h2><a href="/Sinif/1.2/Duyuru/10">Vize Tarihi</a></h2>
          <div class="icerik"><strong>ZZT 101E Sentetik Giris</strong> / <a href="/Sinif/1.2">CRN 12345</a>
            Sinav salonu duyurulacak. <span class="tarih">07 Ekim 2030 15:20</span></div>
          <div class="tarih"><span class="tarih">07 Ekim 2030 15:20</span><span class="tarih">Ogretim Uyesi</span></div></div>
        <div class="duyuruGoruntule"><h2><a href="/Sinif/1.2/Duyuru/11">Yazar Sonda Tarih</a></h2>
          <div class="icerik">Govde <a href="/Sinif/1.2/Duyuru/11">devam</a></div>
          <div class="tarih"><span class="tarih">Baska Uye</span><span class="tarih">08 Ekim 2030 09:00</span><span class="tarih"> </span></div></div>
        <div class="duyuruGoruntule"><h2>Baglantisiz baslik</h2><div class="icerik">atlanir</div></div>
        <div class="duyuruGoruntule"><h2><a href="/Sinif/1.2/Duyuru/12">Icerik yok</a></h2>
          <div class="tarih"><span class="tarih">09 Ekim 2030</span></div></div>""",
    "announcements_fallback": """
        <div><h2><a href="/Sinif/1.2/Duyuru/20">Yedek Kart</a></h2><div class="icerik"><strong>Ders</strong> metin</div></div>
        <h2><a href="/Sinif/1.2/duyuru/21">Kapsayici yok</a></h2>""",
    "announcement_detail_card": """
        <html><head><title>Sayfa</title></head><body><h1>Ust Baslik</h1>
        <div class="duyuruGoruntule"><h2>Kart Basligi</h2>
          <div class="icerik"><span class="tarih">07 Ekim 2030 15:20</span> Duyuru govdesi. Yardım masasi kapali.
            <span class="tarih">08 Ekim 2030 10:00</span></div>
          <div class="tarih"><span class="tarih">Yazar</span></div></div>
        <div>Yardım Diğer sınıflarınıza</div></body></html>""",
    "announcement_detail_card_outer_date": """
        <h1>Ust</h1><div class="duyuruGoruntule"><h2> </h2><span class="tarih">tarih degil</span>
        <span class="tarih">07 Ekim 2030 15:20</span><div class="icerik">Govde</div></div>""",
    "announcement_detail_flat": """
        <html><head><title>Sayfa Basligi</title></head><body><h1>Duz Baslik</h1>
        <p>07 Ekim 2030 15:20</p><p>Govde metni. Yardım masasi.</p>
        <div>Yardım Diğer sınıflarınıza erisin</div><footer>Yardım</footer></body></html>""",
    "announcement_detail_flat_help_word": "<h1>Baslik</h1><p>Tarihsiz govde</p><footer>Yardım Hakkinda</footer>",
    "announcement_detail_card_without_content": '<h1>B</h1><div class="duyuruGoruntule"><h2>K</h2></div><p>01 Ocak 2031 metin</p>',
    # ------------------------------------------------------------ assignments
    "assignments_grid": """
        <table id="ctl00_gvOdevListesi"><tr><td>
          <h2><a href="/Sinif/1.2/Odev/5">Odev Bir</a></h2>
          Ders : ZZT 101E Sentetik Giris Sınıf : <a href="/Sinif/1.2">CRN 12345</a>
          Teslim Başlangıcı : 01 Ekim 2030 00:00 Teslim Bitişi : 15 Ekim 2030 23:59
          Ödevde istenen toplam 3 adet dosyanın 1 adedini yüklediniz.
          <a href="/Sinif/1.2/Odev/5">Ödevi Görüntüle</a> <a href="/Sinif/1.2/Odev/5/OdevGonder">Ödevi Yükle</a>
        </td></tr><tr><td>
          <h2><a href="/Sinif/1.2/Odev/6">Odev Iki</a></h2>
          Teslim Bitişi : 20 Ekim 2030 23:59 <strong class="uyari">4 adet</strong><strong class="uyari">2</strong>
          <strong class="uyari">yok</strong> <a href="/Sinif/1.2/Odev/6">Ödevi Görüntüle</a>
        </td></tr><tr><td>Baslik yok</td></tr><tr><td><h2><a href="/Sinif/1.2/Odev/7">Odev Uc</a></h2></td><td>fazla</td></tr></table>""",
    "assignments_data_class": '<table class="grid data"><tr><td><h2><a href="/Odev/1">Sinifli</a></h2>Ders : A</td></tr></table>',
    "assignments_fallback": """
        <table><tr><td>ilgisiz</td></tr></table>
        <table><tr><td><h2><a href="/Sinif/1.2/odev/9">Yedek</a></h2>Sınıf : B Teslim Başlangıcı : x</td></tr></table>""",
    "assignment_detail": """
        <html><head><title>Odev</title></head><body><h1>Odev Detay</h1>
        <p>Teslim Başlangıcı 01 Ekim 2030 00:00 Teslim Bitişi 15 Ekim 2030 23:59
        <a href="/Sinif/1.2/Odev/5/OdevGonder">Ödevi Yükle</a></p>
        <p>Ödev Açıklaması Raporu yukleyin.</p>
        <h2>Kaynak Dosyalar</h2>
        <table><tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>
          <tr><td><a href="/Sinif/1.2/Odev/5/veri.csv">veri.csv</a></td><td>2 KB</td><td>01 Ekim 2030</td></tr>
          <tr><td>baglantisiz.txt</td><td>1 KB</td><td>02 Ekim 2030</td><td>fazla</td></tr>
          <tr><td>kisa</td><td>satir</td></tr></table>
        <h2>İstenen Dosyalar</h2>
        <table><tr><th>Açıklama</th><th>Uzantılar</th></tr>
          <tr><td>Rapor</td><td>*.pdf</td></tr><tr><td>tek</td></tr><tr><td>Kod</td><td>*.zip</td><td>fazla</td></tr></table>
        <p>Yardım</p></body></html>""",
    "assignment_detail_bare": "<html><head><title>Sadece Baslik</title></head><body><p>Ödev Açıklaması</p></body></html>",
    "upload_statuses": """
        <html><head><title>Yukleme</title></head><body><h1>Odev Gonder</h1>
        <form action="./OdevGonder" method="POST" enctype="multipart/form-data">
        <input type="hidden" name="__VIEWSTATE" value="abc"><input type="hidden" name="__EVENTVALIDATION">
        <input type="hidden" value="adsiz"><input type="hidden" name="" value="bos ad">
        <a href="javascript:__doPostBack('ctl00$Header1$lbDil','')">English</a>
        <a href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$lbIptal','')">Vazgec</a>
        <a href="/Sinif/1.2">Geri</a>
        <a href="javascript:__doPostBack( 'ctl00$ContentPlaceHolder1$lbEkle' , '' )">Ödevi Gönder</a>
        <table><tr><th>Açıklama</th><th>Uzantılar</th></tr>
          <tr><td><strong>Rapor</strong> Bu dosyayı henüz göndermediniz. <input type="file" name="f1" id="id1"></td><td>*.pdf; *.docx</td></tr>
          <tr><td><strong>Kod</strong> Bu dosyayı gönderdiniz. <a href="javascript:void(0)">sil</a>
              <a href="/dosya/kod.zip">kod.zip</a></td><td>.zip, tar <input type="file" name="f2"></td></tr>
          <tr><td>Veri <a href="/dosya/veri.csv">veri.csv</a></td><td>*.csv .</td><td><input type="file" id="id3"></td></tr>
          <tr><td>Aciklamasiz</td><td></td></tr>
          <tr><td>tek hucre</td></tr>
          <tr><td>Dosyayı yüklediniz</td><td>*.PDF</td></tr>
          <tr><td>Odevi teslim ettiniz <a href="/x"> </a></td><td>-</td></tr>
        </table></form></body></html>""",
    "upload_fallback_postback": """
        <form><a href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$lbKaydet','')"></a>
        <a href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$lbDiger','')">Diger</a></form>""",
    "upload_label_match": """<form action="/hedef"><a href="javascript:__doPostBack('ctl00$x$btn','arg')">GÖNDER</a></form>""",
    "upload_submit_button": """
        <form method="get"><button name="btnGonder">Yukle  simdi</button><input type="submit" name="ikinci" value="Gonder"></form>""",
    "upload_submit_input": '<form><input type="submit" name="gonder" value="Kaydet"></form>',
    "upload_no_control": '<form><a href="javascript:__doPostBack(\'ctl00$Header1$lbDil\',\'\')">English</a><input type="submit"></form>',
    # ------------------------------------------------------------------ files
    "file_directory": """
        <table><tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>
          <tr><td><img src="/images/ds/folder.png?v=2"><a href="/Sinif/1.2/DersDosyalari/klasor">Hafta 1</a></td><td></td><td>01 Ekim 2030</td></tr>
          <tr><td><img src="/images/ds/pdf.png"><a href="/Sinif/1.2/DersDosyalari/a.pdf">a.pdf</a></td><td>1 MB</td><td>02 Ekim 2030</td><td>fazla</td></tr>
          <tr><td><a href="/Sinif/1.2/DersDosyalari/ikonsuz">ikonsuz</a></td><td>3 KB</td><td>03 Ekim 2030</td></tr>
          <tr><td>baglantisiz</td><td>1</td><td>2</td></tr>
          <tr><td>iki</td><td>hucre</td></tr><tr></tr></table>""",
    "file_directory_empty": """
        <table><tr><th>Dosyalar</th><th>Boyut</th><th>Tarih</th></tr>
        <tr><td colspan="3">Bu klasörde herhangi bir dosya bulunmamaktadır.</td></tr></table>""",
    # ------------------------------------------------------------ course info
    "course_info_full": """
        <html><head><title>Ders Bilgileri</title></head><body><h1>ZZT 101E</h1>
        <table><tr><td>Hoş Geldiniz Sentetik Ogrenci</td></tr></table>
        <table><tr><td>Ders Adı</td><td>Türkçe</td><td>Sentetik Giris</td></tr>
          <tr><td>İngilizce</td><td>Synthetic Intro</td></tr><tr><td>Kod</td><td>ZZT 101E</td></tr>
          <tr><td>a</td><td>b</td><td>c</td></tr><tr><td>İngilizce</td></tr></table>
        <table><tr><td>Dönem</td><td>2030 Güz</td></tr><tr><td>tek</td></tr></table>
        <table><tr><td>Pazartesi 08:30</td><td>BBB 101</td><td>fazla</td></tr><tr><td>tek</td></tr></table>
        <table><tr><td>Kredi</td><td>3</td></tr><tr><td>x</td><td>y</td><td>z</td></tr></table>
        <table><tr><th>Hafta</th><th>Konu</th></tr><tr><td>1</td><td>Giris</td></tr></table>
        <table><tr><td>altinci</td></tr></table></body></html>""",
    "course_info_headers_first": """
        <table><tr><th>Alan</th><th>Deger</th></tr><tr><td>Kod</td><td>ZZT 1</td></tr></table>
        <table><tr><th>Alan</th><th>Deger</th></tr><tr><td>Donem</td><td>Guz</td></tr></table>
        <table><tr><th>Saat</th><th>Yer</th></tr><tr><td>08:30</td><td>A1</td></tr></table>
        <table><tr><th>K</th><th>V</th></tr><tr><td>k</td><td>v</td></tr></table>""",
    "course_info_two_tables": "<table><tr><td>Dersler Yardım Hakkında Ninova</td></tr></table><table><tr><td>Kod</td><td>ZZT 1</td></tr></table><table><tr><td>Donem</td><td>Guz</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>",
    "course_sections": """
        <a href="/Sinif/1234.5678">Ana</a><a href="/Sinif/1234.5678/Duyurular">Duyurular</a>
        <a href="/Sinif/1234.5678/Duyurular">Tekrar</a><a href="/Sinif/1234.5678/Bos"> </a>
        <a href="/Sinif/1234.56789/Baska">Baska ders</a><a href="/Sinif/9.9/Odevler">Diger</a>
        <a href="Odevler/">Goreli</a>""",
    # ----------------------------------------------------------------- grades
    "gradebook": """
        <table><tr><th>Baska</th></tr><tr><td>x</td></tr></table>
        <table><tr><th>Sentetik Ogrenci</th><th>Not</th><th>Açıklama</th></tr>
          <tr><td>Vize 1</td><td>85</td><td>Iyi</td><td>fazla</td></tr><tr><td>Odev</td><td></td></tr>
          <tr><td>tek</td></tr><tr><td>Ağırlıklı Ortalamanız</td><td>82,5</td></tr>
          <tr><td>Final</td><td>70</td><td></td></tr></table>""",
    "gradebook_no_student": "<table><tr><th>Açıklama</th><th>Not</th></tr><tr><td>Quiz</td><td>10</td></tr><tr><td>Ağırlıklı Ortalamanız</td><td></td></tr></table>",
    "gradebook_not_first": "<table><tr><th>Not</th></tr><tr><td>Quiz</td><td>5</td></tr></table>",
    # ---------------------------------------------------------- message board
    "message_board": """
        <table><tr><th>Mesaj Başlığı</th><th>Son Mesaj</th></tr>
          <tr><td><a href="/Sinif/1.2/MesajPanosu/7">Odev 4</a> soru ozeti</td><td>Sentetik Yazar 29 Mart 2030 22:00</td><td>fazla</td></tr>
          <tr><td>Baglantisiz konu</td><td>Tarihsiz yazar</td></tr>
          <tr><td><a href="/Sinif/1.2/MesajPanosu/8">Sadece baslik</a></td><td></td></tr>
          <tr><td>tek</td></tr></table>""",
    "message_thread": """
        <html><head><title>Konu</title></head><body><h2>Odev 4</h2>
        <table><tr><th>Gönderen</th><th>Mesaj</th></tr>
          <tr><td>Sentetik Yazar 29 Mart 2030 22:00</td><td>Ilk mesaj <a href="/dosya/ek.pdf">ek.pdf</a></td></tr>
          <tr><td>Mesaj</td><td>ara baslik</td></tr><tr><td>29 Mart 2030 23:00</td><td>Yazarsiz</td><td>fazla</td></tr>
          <tr><td>Tarihsiz Yazar</td><td>Ikinci</td></tr><tr><td>tek</td></tr></table></body></html>""",
    "message_thread_no_table": "<html><head><title>Bos Konu</title></head><body><p>mesaj yok</p></body></html>",
    # ------------------------------------------------------------- attendance
    "attendance_kepler": """
        <html><body><p class="uyari"> Iki sistem farkli olabilir. </p>
        <table><tr><th>Öğrenci</th></tr><tr><td>Sentetik Ogrenci</td></tr></table>
        <h2>Kepler Yoklama Bilgileri</h2>
        <table><tr><th>Hafta</th><th>Sali</th><th>Persembe</th></tr>
          <tr><td>1. Hafta</td><td title="Geldi">1 1</td><td>0</td></tr>
          <tr><td>2. Hafta</td><td>0 0</td><td><img alt="Girilmedi" title="Girilmedi" src="x.png">0</td></tr>
          <tr><td>3. Hafta</td><td>1</td></tr>
          <tr><td>4. Hafta</td><td>1</td><td>0</td><td>1</td></tr>
          <tr></tr><tr><td>Hafta yok</td><td>-</td><td></td></tr>
          <tr><td>0. Hafta</td><td>0</td><td>0</td></tr>
          <tr><td>14. Hafta</td><td>1 0</td><td>0 1</td></tr></table></body></html>""",
    "attendance_ninova": """
        <h2>Ninova Yoklama Bilgileri</h2>
        <table><tr><th>Hafta</th><th>Ders</th></tr><tr><td>1</td><td>1</td></tr><tr><td>2</td><td>0</td></tr></table>
        <h2>Kepler Yoklama Bilgileri</h2>
        <table><tr><th>Hafta</th><th>Ders</th></tr><tr><td>1</td><td>0</td></tr></table>""",
    "attendance_unknown_source": "<table><tr><th>Öğrenci</th></tr></table><table><tr><th>Hafta</th><th>A</th></tr><tr><td>1. Hafta</td><td>1 0 1</td></tr></table>",
    "attendance_student_only": "<table><tr><th>Öğrenci</th></tr><tr></tr></table>",
    # -------------------------------------------------------- remote learning
    "remote_with_headers": """
        <h2>Aktif Uzaktan Eğitim Oturumlarınız</h2>
        <table><tr><th>Başlık</th><th>Başlangıç</th><th>Bitiş</th><th>Bağlantı</th><th>Kayıt</th></tr>
          <tr><td>Canli Ders</td><td>17 Eylül 2030 10:00</td><td>17 Eylül 2030 12:00</td><td><a href="/join/1">Katıl</a></td>
              <td><a href="/kayit/1">izle</a></td><td>fazla</td></tr>
          <tr><td>Kisa</td><td></td></tr><tr></tr>
          <tr><td colspan="5">Herhangi bir uzaktan eğitim oturumu bulunmamaktadır</td></tr></table>
        <h2>Sınıfın Geçmiş Uzaktan Eğitim Oturumları</h2>
        <table><tr><td><a href="/gecmis/1">Gecmis oturum</a></td></tr><tr><td>Baglantisiz</td></tr>
          <tr><td>iki</td><td>hucre</td></tr><tr><td> </td></tr><tr><td>No sessions found</td></tr>
          <tr><td><table><tr><td>ic tablo</td></tr></table></td></tr></table>""",
    "remote_english_headers": """
        <h2>Aktif Uzaktan Eğitim Oturumlarınız</h2>
        <table><tr><th>Session</th><th>Start Time</th><th>End</th><th>Link</th></tr>
        <tr><td>Lecture</td><td>bad date</td><td>18 Eylül 2030</td><td>no anchor</td></tr></table>""",
    "remote_headings_only": "<h2>Aktif Uzaktan Eğitim Oturumlarınız</h2><p>tablo yok</p>",
    # ------------------------------------------------------------- OBS public
    "select_options": """
        <select id="DersBransKoduId"><option value="">Seciniz</option><option value=" 304 ">ZZT - Sentetik  Bilimler</option>
        <option>degersiz</option><option value="7"></option></select><select id="Baska"><option value="1">x</option></select>""",
    "course_search_table": """
        <table><tr><th>Ders Kodu</th><th>Ders Adı</th><th>Dil</th></tr>
          <tr><td><a href="/public/DersBilgi/1">ZZT 101E</a></td><td>Sentetik Giris</td><td>EN</td><td>fazla</td></tr>
          <tr><td>ZZT 201 / QQQ 201E, ZZT 201</td><td>Capraz Ders</td></tr>
          <tr><td>kod degil</td><td>Atlanir</td></tr><tr><td>tek</td></tr>
          <tr><td>ZZT 301</td><td><table><tr><th>Ic</th></tr><tr><td>ZZT 999</td><td>ic satir</td></tr></table></td></tr></table>""",
    "course_search_english": "<table><tr><th>Course Code</th><th>Course Name</th></tr><tr><td>zzt 401e</td><td>Synthetic Design</td></tr></table>",
    "course_search_table_then_links": """
        <table><tr><th>Ders Kodu</th><th>Ders Adı</th></tr><tr><td>-</td><td>kod yok</td></tr></table>
        <p>ZZT 101E <a href="/public/DersBilgi/1">Sentetik Giris</a></p><p><a href="/public/DersOnsart?x=1">QQQ 202</a></p>
        <p><a href="/public/DersBilgi/3"> </a><a href="/public/Baska">ilgisiz</a></p>
        <div><a href="/public/DersBilgi/4">Kodsuz baglanti</a></div>""",
    "prerequisites_table": """
        <html><head><title>Onsart</title></head><body>
        <table><tr><th>Ders Kodu</th><th>Ders Adı</th><th>Grup No</th><th>Tip</th></tr>
          <tr><td> ZZT 101E </td><td>Sentetik Giris</td><td>1</td><td>Onsart</td></tr>
          <tr><td></td><td></td><td></td><td></td></tr><tr><td>ZZT 102</td><td>Kisa satir</td></tr>
          <tr><td>ZZT 103</td><td>Uzun</td><td>2</td><td>veya</td><td>fazla</td></tr></table>
        <table><tr><th>On Sart Ders Kodu</th><th>On Sart Ders Adı</th><th>Grup</th><th>Tür</th></tr>
          <tr><td>QQQ 201</td><td>Yazma</td><td>A</td><td>T</td></tr></table>
        <table><tr><th>DersKodu</th><th>Ders Adi</th><th>Tur</th></tr><tr><td>QQQ 301</td><td>Okuma</td><td>S</td></tr></table>
        <table><tr><th>Ilgisiz</th></tr><tr><td>x</td></tr></table>
        <table><tr><td>basliksiz</td><td>Ders Kodu</td></tr></table></body></html>""",
    "prerequisites_text_only": "<html><head><title>T</title></head><body><p>Bu dersin Ön Şart bilgisi asagidadir.</p><table><tr><td>a</td></tr></table></body></html>",
    "prerequisites_none": "<html><head><title>T</title></head><body><p>Kayit bulunamadi.</p></body></html>",
    "schedule_rows": """
        <table id="dersProgramContainer"><thead><tr><td>CRN</td><td>Ders Kodu</td><td>Ders Adı</td><td>Öğretim Yöntemi</td>
        <td>Öğretim Üyesi</td><td>Bina</td><td>Gün</td><td>Saat</td><td>Derslik</td><td>Kontenjan</td><td>Yazılan</td>
        <td>Rezervasyon</td><td>Programlar</td><td>Önşartlar</td><td>Kredi Önşartı</td></tr></thead><tbody>
        <tr><td>30101</td><td><a href="/public/DersBilgi?bransKodu=ZZT&dersNo=101E">ZZT 101E</a></td><td>Sentetik Giris</td>
            <td>Fiziksel (Yüz yüze)</td><td>Sentetik Hoca</td><td><a href="/b">BBB</a><br/><a href="/b">CCC</a></td>
            <td>Pazartesi<BR>Çarşamba</td><td>08:30/10:29<br >13:30/15:29</td><td>A101<br/>--</td><td>40</td><td>35</td>
            <td>ZZT_LS/8/10 | Diğer/41/37</td><td><a href="/p">ZZT, QQQ, ZZT, -</a></td><td><a href="/onsart?x=1">Detay</a></td>
            <td><a href="/kredi?x=1">Detay</a></td></tr>
        <tr><td>30102</td><td>ZZT 102</td><td>Yersiz Ders</td><td>Ek Sınav</td><td>-</td><td>--</td><td>-</td><td>-/-</td><td>--</td>
            <td>-5</td><td>--3</td><td>-</td><td>ZZT, QQQ , ,</td><td>-</td><td>-</td></tr>
        <tr><td>30103</td><td><a href="/public/DersBilgi">ZZT 103</a></td><td>Bina Bekleyen</td><td>Uzaktan</td><td>Undeclared</td>
            <td>--<br/>BBB</td><td>Salı<br/>Perşembe</td><td>09:30/11:29<br/>09:30/11:29</td><td>--<br/>--</td><td>x</td><td></td>
            <td>bozuk bicim</td><td>-</td><td>ZZT 101E MIN DD</td><td>4. sinif</td></tr>
        <tr><td>30104</td><td>ZZT 104</td><td>On Hucre</td><td>Fiziksel</td><td>Hoca</td><td>BBB</td><td>Cuma</td><td>10:30/12:29</td>
            <td>A1</td><td>20</td></tr>
        <tr><td>30105</td><td>ZZT 105</td><td>On Bir</td><td>Fiziksel</td><td>Hoca</td><td>BBB</td><td>Cuma</td><td></td>
            <td>A1</td><td>20</td><td>5</td></tr>
        <tr><td>30106</td><td>ZZT 106</td><td>On Iki</td><td>Fiziksel</td><td>Hoca</td><td></td><td></td><td></td>
            <td></td><td>--7</td><td>5</td><td>Diğer/10/12</td></tr>
        <tr><td>30107</td><td>ZZT 107</td><td>On Uc</td><td>Fiziksel</td><td>Hoca</td><td>BBB</td><td>Cuma</td><td>10:30/12:29</td>
            <td>A1</td><td>20</td><td>5</td><td>-</td><td></td></tr>
        <tr><td>30108</td><td>ZZT 108</td><td>On Dort</td><td>Fiziksel</td><td>Hoca</td><td>BBB</td><td>Cuma</td><td>10:30/12:29</td>
            <td>A1</td><td>20</td><td>5</td><td>-</td><td>ZZT</td><td></td></tr>
        <tr><td>30109</td><td>ZZT 109</td><td>On Alti</td><td>Fiziksel</td><td>Hoca</td><td>BBB</td><td>Cuma</td><td>10:30/12:29</td>
            <td>A1</td><td>20</td><td>5</td><td>-</td><td>ZZT</td><td>-</td><td>-</td><td>fazla sutun</td></tr>
        <tr><td>kisa</td><td>satir</td></tr><tr></tr>
        </tbody></table>""",
    "schedule_fallback_table": """
        <table><tr><td>ilgisiz</td></tr></table>
        <table><thead><tr><td>CRN</td><td>Ders Kodu</td></tr></thead><tbody>
        <tr><td>30201</td><td>ZZT 201</td><td>Yedek Tablo</td><td>Fiziksel</td><td>Hoca</td><td>BBB</td><td>Cuma</td>
        <td>10:30/12:29</td><td>A1</td><td>20</td><td>5</td></tr></tbody></table>""",
    "schedule_fallback_no_thead": """
        <table><tr><td>CRN</td><td>Ders Kodu</td></tr><tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td>
        <td>8</td><td>9</td><td>10</td></tr></table>""",
    "schedule_no_tbody": '<table id="dersProgramContainer"><thead><tr><td>CRN</td><td>Ders Kodu</td></tr></thead></table>',
    "schedule_no_thead": """
        <table id="dersProgramContainer"><tbody><tr><td>30301</td><td>ZZT 301</td><td>Basliksiz</td><td>Fiziksel</td><td>Hoca</td>
        <td>BBB</td><td>Cuma</td><td>10:30/12:29</td><td>A1</td><td>20</td><td>5</td></tr></tbody></table>""",
    "schedule_not_found": "<div>Aradığınız kriterlere uygun ders bulunamadı.</div>",
    "schedule_kayit_message": "<div>Herhangi bir kayit yok.</div>",
    "schedule_unrecognized": "<div>Baska bir sayfa</div>",
    # ----------------------------------------------------------------- portal
    "portal_full": """
        <div data-placement="balance"> ₺ 42 </div>
        <ul data-placement="transitions">
          <li class="card-deposit__list-item"><span class="amount">Bakiye</span><span class="amount">Tutar</span></li>
          <li class="card-deposit__list-item"><span class="icon-itu-card-spending text-danger"></span><span>Harcama</span>
              <span class="amount">₺ 42</span><span class="amount text-danger"> ₺ -47,5</span></li>
          <li class="card-deposit__list-item"><span class="text-success icon-itu-card-charge"></span><span>Yükleme</span>
              <span class="amount">₺ 89,5</span></li>
          <li class="card-deposit__list-item"><span class="amount">₺ 1</span><span class="icon-diger"></span>Diger</li>
          <li class="card-deposit__list-item">Siniflandirilmamis</li>
          <li class="baska">atlanir</li></ul>
        <ul data-placement="notification-list">
          <li class="notification__list-item notification__list-item--unread"><a href="#" data-notification-id="1">
              <span class="pull-left">Ag Calismasi</span><span class="pull-right">2 s </span></a></li>
          <li class="notification__list-item"><a href="#"><span class="pull-left">Zamansiz</span></a></li>
          <li class="notification__list-item"><a href="#"><span class="pull-right">Basliksiz</span></a></li>
          <li class="notification__list-item"><span class="pull-left">Baglantisiz</span></li>
          <li class="notification__list-item"><a href="#"><span class="pull-left"> </span></a></li></ul>
        <span data-placement="food-title"> Akşam Yemeği Menüsü </span>
        <input type="text" id="food-date" value="10.07.2030">
        <input type="radio" id="radio-ogle"><input type="radio" id="radio-aksam" checked="checked">
        <input type="checkbox" id="checkbox-vejeteryan-vegan" checked="checked">
        <ul data-placement="food-list">
          <li class="lunch-menu__list-item">Mercimek Corbasi <i class="icon-warning" data-food-id="77"></i></li>
          <li class="lunch-menu__list-item"><i class="icon-warning"></i>Pilav</li>
          <li class="lunch-menu__list-item"> </li><li class="diger">sinifsiz</li>
          <li><b>Seçmeli 4. Çeşit</b><ul class="secmeli-yemek">
              <li class="lunch-menu__list-item">Ayran</li><li class="lunch-menu__list-item"> </li>
              <li class="lunch-menu__list-item">Meyve</li></ul></li></ul>""",
    "portal_lunch_checked": """
        <input type="radio" id="radio-ogle" checked="checked"><input type="radio" id="radio-aksam">
        <input id="food-date" value=""><ul data-placement="food-list"><li class="lunch-menu__list-item">Corba</li>
        <b>Seçmeli</b><ul class="baska"><li class="lunch-menu__list-item">Tatli</li></ul></ul>""",
    "portal_dinner_no_title": '<input type="radio" id="radio-aksam" checked="checked"><ul data-placement="food-list"><b>Bos Secmeli</b><ul class="secmeli-yemek"></ul></ul>',
    "portal_bare_checked": '<input type="radio" id="radio-ogle" checked><input type="checkbox" id="checkbox-vejeteryan-vegan"><span data-placement="food-title">Öğle Yemeği</span><input id="food-date"><ul data-placement="food-list"><li class="lunch-menu__list-item">Tek yemek</li></ul>',
    "portal_many": (
        '<ul data-placement="transitions">'
        + "".join(f'<li class="card-deposit__list-item"><span class="amount">₺ {n}</span>Islem {n}</li>' for n in range(23))
        + '</ul><ul data-placement="notification-list">'
        + "".join(
            f'<li class="notification__list-item"><a href="#" data-notification-id="{n}"><span class="pull-left">Bildirim {n}</span></a></li>'
            for n in range(23)
        )
        + "</ul>"
    ),
}


def _mutate(html: str, variant: str) -> str:
    """Malformed versions of a page: tables gone, a cell short, a cell extra."""
    soup = BeautifulSoup(html, "lxml")
    if variant == "no_tables":
        for table in soup.find_all("table"):
            table.decompose()
    elif variant == "drop_last_cell":
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if cells:
                cells[-1].decompose()
    elif variant == "extra_cell":
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if cells:
                extra = soup.new_tag(cells[-1].name)
                extra.string = "EK"
                cells[-1].insert_after(extra)
    else:
        raise ValueError(variant)
    return str(soup)


MUTATIONS = ("no_tables", "drop_last_cell", "extra_cell")


def documents() -> Iterator[tuple[str, str]]:
    """Every page of the corpus as ``(name, html)``: fixtures, synthetic pages, mutations."""
    originals: list[tuple[str, str]] = []
    for path in sorted(FIXTURES.glob("*.html")):
        if path.name not in SKIPPED_FIXTURES:
            originals.append((f"fixture:{path.name}", path.read_text(encoding="utf-8")))
    originals.extend((f"synthetic:{name}", html) for name, html in SYNTHETIC.items())
    for name, html in originals:
        yield name, html
        if len(html) > MUTATION_SIZE_LIMIT or "<t" not in html:
            continue
        for variant in MUTATIONS:
            yield f"{name}|{variant}", _mutate(html, variant)


def _attendance_with_calendar(html: str) -> dict[str, Any]:
    result = parsing.extract_attendance(html, PAGE, BASE)
    return parsing.apply_attendance_calendar(result, term_start=TERM_START, today=date(2030, 9, 12))


def _dashboard(html: str) -> dict[str, Any]:
    page = parsing.parse_html_page(PAGE, html, base_url=BASE)
    return parsing.summarize_dashboard(page, html, BASE, today=TODAY)


def _snapshot(html: str) -> dict[str, Any]:
    return parsing.make_snapshot_payload(parsing.parse_html_page(PAGE, html, base_url=BASE), label="etiket")


# Parsers that take a whole page. The two that only delegate to another module
# (academic calendar, help tickets) are left to that module's own tests.
PAGE_PARSERS: dict[str, Callable[[str], Any]] = {
    "parse_html_page": lambda html: parsing.parse_html_page(PAGE, html, base_url=BASE),
    "make_snapshot_payload": _snapshot,
    "summarize_dashboard": _dashboard,
    "extract_courses": lambda html: parsing.extract_courses(html, PAGE, BASE),
    "extract_named_table:Son Odevler": lambda html: parsing.extract_named_table(html, "Son Odevler"),
    "extract_announcements_list": lambda html: parsing.extract_announcements_list(html, PAGE, BASE),
    "extract_announcement_detail": lambda html: parsing.extract_announcement_detail(html, PAGE, BASE),
    "extract_assignments_list": lambda html: parsing.extract_assignments_list(html, PAGE, BASE),
    "extract_assignment_detail": lambda html: parsing.extract_assignment_detail(html, PAGE, BASE),
    "extract_assignment_upload_status": lambda html: parsing.extract_assignment_upload_status(html, PAGE, BASE),
    "extract_assignment_upload_form": lambda html: parsing.extract_assignment_upload_form(html, PAGE, BASE),
    "extract_file_directory": lambda html: parsing.extract_file_directory(html, PAGE, BASE, "/Hafta 1/"),
    "extract_file_directory:root": lambda html: parsing.extract_file_directory(html, PAGE, BASE),
    "extract_course_info": lambda html: parsing.extract_course_info(html, PAGE, BASE),
    "extract_course_sections": lambda html: parsing.extract_course_sections(html, PAGE, BASE),
    "extract_gradebook": lambda html: parsing.extract_gradebook(html, PAGE, BASE),
    "extract_message_board": lambda html: parsing.extract_message_board(html, PAGE, BASE),
    "extract_message_thread_detail": lambda html: parsing.extract_message_thread_detail(html, PAGE, BASE),
    "extract_attendance": lambda html: parsing.extract_attendance(html, PAGE, BASE),
    "apply_attendance_calendar": _attendance_with_calendar,
    "extract_remote_learning": lambda html: parsing.extract_remote_learning(html, PAGE, BASE),
    "extract_course_select_options": lambda html: parsing.extract_course_select_options(html, PAGE),
    "extract_course_search_results": lambda html: parsing.extract_course_search_results(html, PAGE),
    "extract_prerequisite_list": lambda html: parsing.extract_prerequisite_list(html, PAGE, BASE),
    "extract_course_schedule_table": lambda html: parsing.extract_course_schedule_table(html, PAGE, BASE),
    "extract_campus_card_info": lambda html: parsing.extract_campus_card_info(html, PAGE),
    "extract_notifications": lambda html: parsing.extract_notifications(html, PAGE),
    "extract_cafeteria_menu": lambda html: parsing.extract_cafeteria_menu(html, PAGE),
}

_SLOTS = [
    {"index": 1, "description": "Rapor"},
    {"index": 2, "description": "Rapor Eki"},
    {"index": 3, "description": "Kaynak Kod"},
    {"index": 4, "description": "KAYNAK KOD"},
    {"index": 5},
]
_DATE_TEXTS = [
    None, "", "   ", "29 Mart 2026 22:00", "29 Mart 2026", "1 Şubat 2031 09:05", "Son: 07 Ekim 2030 15:20 sonra",
    "29 Smarch 2026", "31 Şubat 2030 10:00", "07 Ekim 15:20", "07 Ekim", "29 Şubat", "29 Şubat 12:00", "7 Uydurma 10:00",
    "Yazar Adi 29 Mart 2026 22:00", "29 Mart 2026 22:00 Yazar", "tarih yok", "25 Şubat - 31 Aralık 00:00 18:00",
    "25 Şubat 00:00 - 31 Aralık 18:00", "25 Şubat 2031 - 31 Aralık 2031 00:00 18:00", "25 Şubat - 31 Aralık",
    "1 Ocak 2031 10:00 - 2 Ocak 2031", "12 - 14", "25 Subat - 31 Aralik 00:00", "A - B", "25 Şubat - 31 Aralık 00:00 18:00 ek",
    "01 Ocak 2030 - 05 Xyz 2030", "5 MAYIS 2030 08:00", "5 Mayıs 2030 8:00", "15 AĞUSTOS 2030", "3 Eylül 99",
]
_URL_INPUTS = [
    ("https://example.org/a b", BASE), ("https://example.org/a", BASE), ("/Sinif/1.2", BASE + "/"), ("Sinif/1.2", BASE),
    ("", BASE), ("/a\\b", BASE), ("https://user@example.org/", BASE), ("//example.org/x", BASE), ("?q=1", BASE + "/x/"),
    ("/yol\n", BASE), ("mailto:kisi@example.org", BASE), ("/ok", "https://user:pw@example.org"),
]
_FILENAMES = [None, "", "Çalışma Kağıdı 1.pdf", "a%2Fb%3Fc.txt", "..", " . nokta . ", 'x<>:"/\\|?*y', "a∕b", "çok   boşluk\t.pdf", "\x00\x1f"]


def _call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except Exception as error:  # the error type and text are part of the behaviour
        return {"__raises__": type(error).__name__, "message": str(error)}


def _soup_table(html: str) -> Any:
    return parsing.make_soup(html).find("table")


def helper_cases() -> dict[str, Any]:
    """Outputs of the text, date, URL and slot helpers for a fixed list of inputs."""
    p = parsing
    cases: dict[str, Any] = {}
    for text in _DATE_TEXTS:
        key = repr(text)
        parsed = _call(p.parse_ninova_datetime, text)
        cases[f"parse_ninova_datetime:{key}"] = parsed.isoformat() if hasattr(parsed, "isoformat") else parsed
        cases[f"ninova_datetime_iso:{key}"] = _call(p.ninova_datetime_iso, text)
        cases[f"split_trailing_ninova_datetime:{key}"] = _call(p.split_trailing_ninova_datetime, text)
        cases[f"split_date_range:{key}"] = _call(p.split_date_range, text)
        cases[f"infer_yearless_datetime:{key}"] = _call(p.infer_yearless_datetime, text, today=TODAY)
        cases[f"_looks_like_date:{key}"] = _call(p._looks_like_date, text or "")
        cases[f"clean_text:{key}"] = _call(p.clean_text, text)
        cases[f"normalize_lookup_text:{key}"] = _call(p.normalize_lookup_text, text)
    cases["infer_yearless_datetime:leap"] = p.infer_yearless_datetime("29 Şubat 10:00", today=date(2028, 1, 5))
    cases["infer_yearless_datetime:year_end"] = p.infer_yearless_datetime("02 Ocak", today=date(2030, 12, 30))
    for text in ["İSTANBUL Iğdır", "  Çok\n Boşluk ", "ÖĞRENCİ", "ﬁ ligature Ⅷ", "á"]:
        cases[f"normalize_lookup_text:{text!r}"] = p.normalize_lookup_text(text)
    for url, base in _URL_INPUTS:
        cases[f"normalize_url:{url!r}|{base!r}"] = _call(p.normalize_url, url, base)
    for url in ["https://ninova.itu.edu.tr/Sinif/1.2", "https://ninova.itu.edu.tr/Sinif/1.2/", "/Sinif/1.2/x", "/Sinif/12", "https://example.org/Sinif/1.2", "mailto:a@example.org", "tel:123", "/dosya/A.PDF", "/dosya/a.exe", "https://example.org/a.zip?x=1"]:
        cases[f"is_course_root:{url}"] = p.is_course_root(url)
        cases[f"is_internal_ninova_url:{url}"] = p.is_internal_ninova_url(url, BASE)
        cases[f"link_kind:{url}"] = p.link_kind(url, BASE)
        cases[f"_course_group:{url}"] = p._course_group(url)
    for name in _FILENAMES:
        cases[f"sanitize_filename:{name!r}"] = p.sanitize_filename(name)
        cases[f"slugify:{name!r}"] = p.slugify(name or "")
    for code in ["CEN 2025", "ZZT 101E", "ZZT 1999E", "ZZT2031", "ZZT 4901E", "ZZT 3000"]:
        cases[f"_is_year_like_code:{code}"] = p._is_year_like_code(code)
    for text in ["ZZT 2030 Summer", "ZZT 2030 ve QQQ 101E", "kod yok", "ZZT101 QQQ 202", "zzt 101"]:
        cases[f"_pick_code:{text}"] = list(p._pick_code(text))
    for value in [None, "", "-", "--", "-/-", " – — ", "Undeclared", "UNDECLARED", "A101", "- A"]:
        cases[f"_schedule_value:{value!r}"] = p._schedule_value(value)
    for raw in [None, "", "ZZT_LS/8/10 | Diğer/41/37", "Diğer/5/9", "OTHER / 3 / 1|others/1/1", "ZZT/8", "ZZT/8/10 |", "A/B/1/2", "ZZT/x/1"]:
        cases[f"_parse_schedule_reservation:{raw!r}"] = list(p._parse_schedule_reservation(raw))
    for kwargs in [
        {"slot_index": 2}, {"slot_index": 9}, {"slot_index": 0}, {"slot_description": "rapor"}, {"slot_description": "Rapor Eki"},
        {"slot_description": "kaynak kod"}, {"slot_description": "kaynak"}, {"slot_description": "yok"}, {"slot_description": "  "},
        {}, {"slot_index": 3, "slot_description": "rapor"}, {"slot_description": "eki"},
    ]:
        cases[f"match_upload_slot:{sorted(kwargs.items())}"] = _call(p.match_upload_slot, _SLOTS, **kwargs)
    cases["match_upload_slot:empty"] = _call(p.match_upload_slot, [], slot_index=1)
    cases["match_upload_slot:single"] = _call(p.match_upload_slot, _SLOTS[:1])
    cases["match_upload_slot:single_blank_description"] = _call(p.match_upload_slot, _SLOTS[:1], slot_description="")
    for filename, allowed in [("a.pdf", None), ("a.pdf", []), ("a.PDF", [".pdf"]), ("a.pdf", ["PDF", "zip"]), ("a", [".pdf"]), ("a.tar.gz", [".tar.gz"]), ("a.gz", [".GZ"]), ("a.doc", [".pdf"])]:
        cases[f"extension_allowed:{filename}|{allowed}"] = p.extension_allowed(filename, allowed)
    cases["_extract_labeled_value"] = [
        p._extract_labeled_value("Ders : A Sınıf : B Teslim : C", "Ders :", ["Sınıf :", "Teslim :"]),
        p._extract_labeled_value("Ders : A", "Sınıf :", ["Teslim :"]),
        p._extract_labeled_value("Sınıf :   Teslim : C", "Sınıf :", ["Teslim :"]),
        p._extract_labeled_value("Sınıf : sona kadar", "Sınıf :", ["Teslim :"]),
        p._extract_labeled_value("a.b (c) a.b d", "a.b", ["(c)"]),
    ]
    cases["_remove_once"] = [p._remove_once("a b a", "a"), p._remove_once("a b", None), p._remove_once("a b", "x"), p._remove_once("a", "")]
    cases["_join_virtual_path"] = [p._join_virtual_path("/", "a"), p._join_virtual_path("", "a"), p._join_virtual_path("/x/", "a"), p._join_virtual_path("//", "a")]
    cases["_pairs_from_rows"] = [p._pairs_from_rows([["a", "b"], ["c"], {"d": 1}, ["e", "f", "g"]]), p._pairs_from_rows([])]
    cases["_is_noise_table"] = [
        p._is_noise_table({"rows": [["Hoş Geldiniz", "x"]]}), p._is_noise_table({"rows": [{"a": "Dersler Yardım", "b": "Hakkında Ninova"}]}),
        p._is_noise_table({"rows": []}), p._is_noise_table({"rows": [["a"], {"b": 1}]}),
    ]
    cases["_with_inferred_dates"] = p._with_inferred_dates(
        [["Metin", "07 Ekim 15:20"], ["Metin", "tarih degil"], ["a", "b", "c"], {"Tarih": "12 Kasım", "Diger": 5},
         {"date_iso": "var", "Tarih": "12 Kasım"}, {"Tarih": "01 Ocak 2031"}, "duz metin", {"Ilk": "1 Ocak", "Iki": "2 Ocak"}],
        TODAY,
    )
    table = _soup_table("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>")
    cases["_table_to_rows:ragged"] = p._table_to_rows(table)
    cases["_table_to_rows:th_after_data"] = p._table_to_rows(_soup_table("<table><tr><td>x</td></tr><tr><th>A</th></tr></table>"))
    cases["_table_to_rows:mixed_header"] = p._table_to_rows(_soup_table("<table><tr><th>A</th><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"))
    snapshot_old = {"url": "u", "title": "Eski", "links": [{"url": "a", "text": "A"}, {"url": "b", "text": "B"}],
                    "attachments": [{"url": "f", "text": "F"}], "text": "bir\niki", "text_hash": "1"}
    snapshot_new = {"url": "u", "title": "Yeni", "links": [{"url": "b", "text": "B"}, {"url": "c", "text": "C"}],
                    "attachments": [], "text": "bir\nuc", "text_hash": "2"}
    cases["compare_snapshot_payloads"] = p.compare_snapshot_payloads(snapshot_old, snapshot_new)
    cases["compare_snapshot_payloads:empty"] = p.compare_snapshot_payloads({}, {})
    sections_html = '<a href="/Sinif/1.2/Odevler">Odevler</a>'
    cases["extract_course_sections:not_a_course_page"] = p.extract_course_sections(sections_html, BASE + "/Kampus", BASE)
    attendance = p.extract_attendance(SYNTHETIC["attendance_ninova"], PAGE, BASE)
    cases["apply_attendance_calendar:no_term_start"] = p.apply_attendance_calendar(attendance, term_start=None)
    cases["pretty_json"] = p.pretty_json({"ş": [1, None]})
    return cases


def _dump(value: Any) -> str:
    # Key order is kept: it is part of what callers see.
    return json.dumps(value, ensure_ascii=False, sort_keys=False, default=str)


def record(value: Any, *, page: str = "", parser: str = "") -> Any:
    """What the recorded file keeps for one output: the value, or its digest when large."""
    text = _dump(value)
    title = page.split(":", 1)[-1]
    allow_full = "|" not in page and title.startswith(FULL_OUTPUT_PAGES.get(parser, ("",)))
    if allow_full and len(text) <= FULL_OUTPUT_LIMIT:
        return json.loads(text)
    return {"__sha256__": hashlib.sha256(text.encode("utf-8")).hexdigest()[:20], "chars": len(text)}


def page_cases() -> Iterator[tuple[str, str, Any, bool]]:
    """``(document, parser, output, is_empty_output)`` for every page and parser of the corpus."""
    empty = {parser_name: _dump(_call(parser, "")) for parser_name, parser in PAGE_PARSERS.items()}
    for name, html in documents():
        for parser_name, parser in PAGE_PARSERS.items():
            value = _call(parser, html)
            yield name, parser_name, value, _dump(value) == empty[parser_name]


def build_golden() -> dict[str, Any]:
    empty = {parser_name: record(_call(parser, "")) for parser_name, parser in PAGE_PARSERS.items()}
    pages: dict[str, dict[str, Any]] = {}
    for name, parser_name, value, is_empty in page_cases():
        # Most parsers find nothing on most pages; only the differences are kept.
        if not is_empty:
            pages.setdefault(name, {})[parser_name] = record(value, page=name, parser=parser_name)
    helpers = json.loads(_dump(helper_cases()))
    return {"empty_document": empty, "pages": pages, "helpers": helpers}


def _format_golden(golden: dict[str, Any]) -> str:
    """One recorded output per line, so a changed output is a one-line diff."""
    def block(mapping: dict[str, Any], indent: str) -> str:
        lines = [f"{indent} {json.dumps(key, ensure_ascii=False)}: {_dump(value)}" for key, value in mapping.items()]
        return "{\n" + ",\n".join(lines) + f"\n{indent}}}"

    pages = ",\n".join(
        f"  {json.dumps(name, ensure_ascii=False)}: {block(parsers, '  ')}" for name, parsers in golden["pages"].items()
    )
    return (
        '{\n "empty_document": ' + block(golden["empty_document"], " ")
        + ',\n "pages": {\n' + pages + '\n },\n "helpers": ' + block(golden["helpers"], " ") + "\n}\n"
    )


if __name__ == "__main__":  # python tests/parsing_corpus.py rewrites the recorded outputs
    GOLDEN_PATH.write_text(_format_golden(build_golden()), encoding="utf-8", newline="\n")
    print(f"wrote {GOLDEN_PATH}")
