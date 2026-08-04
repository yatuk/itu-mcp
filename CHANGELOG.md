# Changelog

## v0.5.0

- `plan_remaining_courses`: `obs_get_graduation_remaining`'in kalan zorunlu derslerini arşivden
  mevsimsellik ve `who_taught` geçmişiyle birleştirip her ders için tek satır planlama önerisi
  üretiyor (örn. "CEN 411E yalnızca Güz'de açılıyor, en sık Uyar veriyor, ort. doluluk 0.95").
  Daha önce ders başına elle `archive_course_history` çağırıp birleştirmek gerekiyordu.
- `archive_search_courses(query)`: arşivin tam kod/isim indeksinde arama; yalnızca kod bilinmediğinde
  "sayısal yöntemler" gibi bir isim parçasından doğru kodu buluyor. `obs_search_courses`'tan farklı
  olarak yalnızca aktif dönemi değil, arşivin gördüğü tüm dönemleri tarıyor.
- `archive_list_branches(term)`: bir dönemin dökümünde hangi branşların bulunduğunu doğrudan
  listeliyor; artık `archive_term_sections`'ı branş branş deneyip `coverage` alanına bakmaya gerek yok.
- `archive_compare_terms(course_code, term_a, term_b)`: iki dönem arasında bir dersin
  hoca/kontenjan/doluluk değişimini adlandırarak (eklenen/çıkan hocalar, şube sayısı farkı) döndürüyor.
- `explain_course_eligibility` artık `archive_seasonality` alanı taşıyor: ön şart karşılansa bile
  dersin arşivde yalnızca tek bir dönemde açıldığı otomatik olarak bildiriliyor.

## v0.4.1

- `explain_course_eligibility` artık bağımsız, üçüncü taraf bir topluluk veri setiyle
  çapraz doğrulama yapıyor: OBS'ten çıkarılan kural, bağımsız bir ikinci kaynakla
  karşılaştırılıp `cross_check` alanında raporlanıyor. OBS her zaman yetkili kaynak kalır;
  anlaşmazlık sessizce çözülmez, bildirilir. Kaynağa erişilemezse veya ders veri setinde
  yoksa `available: false` ile bildirilir, ana yanıtı etkilemez.
- `PREREQ_CROSSCHECK_BASE_URL` / `PREREQ_CROSSCHECK_CACHE_TTL_SECONDS` ile yapılandırılabilir.

## v0.4.0

### Arşiv entegrasyonu

- [İTÜ Ders Arşivi](https://github.com/yatuk/itu-archive) araçları: `archive_who_taught`,
  `archive_course_history`, `archive_fill_rate`, `archive_instructor_courses`,
  `archive_term_sections`, `archive_list_terms`. OBS'nin sildiği 27 dönemlik geçmiş
  canlı OBS verisinin yanında sorgulanabiliyor.
- Arşiv sonuçları `coverage` alanı taşıyor: "dönem hiç kaydedilmemiş", "branş dökümde
  yok" ve "filtreye uyan şube yok" durumları ayrı ayrı bildiriliyor; boş sonuç artık
  sessizce "ders açılmadı" gibi okunmuyor.
- Arşiv istemcisi ayrı çerezsiz oturum, HTTPS zorunluluğu ve tek-host allowlist ile
  çalışıyor; `ITU_ARCHIVE_BASE_URL` ve `ITU_ARCHIVE_CACHE_TTL_SECONDS` ile ayarlanabilir.

### Ön şart verisi

- Ön şartlar artık resmî branş tablosundan (`/public/GenelTanimlamalar/OnsartAra`)
  okunuyor: tam Ve/Veya ifadesi, ders bazlı minimum not ve kredi şartı ile.
- `prerequisite_status` alanı "ön şartı yok" (resmî tabloda kanıtlanmış yokluk) ile
  "bilinmiyor" (tablo okunamadı) durumlarını ayırıyor. Boş liste artık iki farklı
  anlama gelmiyor.
- 4 haneli ders kodları (`CEN 4901E`, bitirme tasarımı) ve iki harfli ekler
  (`FIZ 101EL`) destekleniyor; `explain_course_eligibility` bu kodlarda artık hata
  vermiyor.
- `explain_course_eligibility` minimum not şartlarını değerlendiriyor;
  `completed_courses` girdisi `KOD:NOT` biçimini kabul ediyor.

### Eksik veri düzeltmeleri

- `obs_get_graduation_remaining` artık `summary` döndürüyor: hangi gerçek dersin hangi
  seçmeli slotu doldurduğu (`BLG 422E → 7th Sems. Elect. Course I (MT)`), boş slotlar,
  kalan zorunlu dersler ve kredi dökümü.
- `obs_calculate_gpa` notu girilmemiş derslerin kredisini ders planından tamamlıyor;
  hangi derslerin bu yolla çözüldüğü `credit_fallback_courses` ile bildiriliyor.

## v0.3.0

- Public final sınav programı, İTÜ rehberi, bina kodları, mekik saatleri,
  spor tesisi çalışma saatleri ve resmî duyuru toplama araçları
- Ders programından açık kontenjan ve kapsamı açıkça belirtilen boş derslik tahmini
- Fakülte/program/ders planı akışı, önşart uygunluk açıklaması ve kişisel final takvimi
- Public kütüphane arama/kopya durumu; ayrı hesapla ödünç listesi, yenileme ve
  ayırtma için `confirm=true` korumalı akış
- İTÜ Portal yemek menüsünde tarih/öğün/vegan seçimi, besin değerleri ve alerjenler;
  bildirim ve yardım bileti API/fallback desteği
- GPA projeksiyon düzeltmeleri, hedef GPA hesabı, çok bölümlü çakışma taraması ve
  tarih/kategori/sorgu filtreli akademik takvim
- Public istemcilerde ayrı çerezsiz oturumlar, exact HTTPS host allowlist'leri ve
  her yönlendirme hedefini istekten önce doğrulayan SSRF koruması
- Harici içerik sonuçlarında prompt-injection köken işaretleri; kütüphane TLS
  doğrulamasında güvenli hata verme
- README Mermaid mimarisi, araç haritası, ileri kullanım ve güvenlik belgeleri güncellendi
- PyPI kaynak paketi manifesti; Python 3.14, `pip-audit`, Bandit ve Twine kontrollü CI/yayın akışı

## v0.2.2

- README görselleri için absolute GitHub URL'leri (PyPI uyumluluğu)
- MCP Registry'de yayınlandı (`io.github.yatuk/itu-mcp`)
- `server.json` eklendi
- `SERVER_VERSION` 0.2.2'ye güncellendi

## v0.2.1

- Güvenlik: `client.py`'de `_check_domain()` — sadece `*.itu.edu.tr` domain'lerine istek
- Güvenlik: `remote_security.py` API key query param fallback'i kaldırıldı
- Güvenlik: `read_resource_text`, `read_page` remote modda devre dışı (SSRF/path traversal)
- Portal sayfası 5 yerine tek HTTP isteğiyle çekiliyor (`_get_portal_page` cache)
- GPA `credit=0` falsy-zero fix'i
- `is_fully_uploaded` bool kontrolü düzeltildi
- Building adlarında HTML kalıntısı temizlendi

## v0.2.0

- Rebrand package to **itu-mcp** (yatuk).
- OBS student portal: JWT + `/api/ogrenci/*` tools (`obs_*`).
- PDF/DOCX text extraction (`read_resource_text`).
- Assignment upload with dry-run / `confirm=true`.
- Course list TTL cache, request delay, compact responses.
- Session cookie persistence, remote API key + rate limit.
- CLI: `--help`, `--version`, `--check-auth`, `--list-tools`.
- OBS public: ders programı, önşart zinciri, ders arama
- Portal widget'ları: kampüs kart, yemekhane, bildirim, yardım biletleri, kota
- GPA hesaplayıcı, ders çakışma kontrolü, akademik takvim
- Yoklama devamsızlık risk özeti
- Mermaid mimari diyagramı, logo, 55 MCP aracı

## v0.1.x

Upstream lineage: [hikmedit/ninova-mcp](https://github.com/hikmedit/ninova-mcp) through early 0.1.x releases.
