# Changelog

## v0.7.1

README güncellemesi.

- Başlıktaki statik logo, animasyonlu `docs/logo.gif` ile değiştirildi
- `docs/cli_demo.gif` eklendi: `--version`, `--list-tools`, `--list-prompts`
  komutlarının gerçek çıktısıyla oluşturulmuş bir terminal demosu, Örnekler
  bölümüne kondu
- README ton olarak yeniden yazıldı, açılış paragrafı ve birkaç bölüm daha
  samimi bir dille genişletildi; bilinen bir sınırlamaya (OBS uç noktalarının
  hesaba göre tutarsız davranabilmesi) açıkça değinildi
- `estimate_relative_grade` ve yeni prompt/resource'lar için kısa açıklayıcı
  bölümler eklendi, "Ne sorabilirsin?" listesine iki örnek daha kondu

## v0.7.0

### Yeni tool: `estimate_relative_grade`

İTÜ'nün bağıl değerlendirme (T-skor) yönetmeliğindeki iki resmi yöntemi uygular:
sınıfın ham puanlarını ve kendi puanını verince, Yöntem 1 (T-skor + örnek sınıf-düzeyi
tablosu) ve Yöntem 2 (sınıf ortalaması ± standart sapma katları) ile muhtemel harf
notunu tahmin eder. İki yöntem farklı sonuç verebilir — ikisi de raporlanır.

- Tamamen yerel hesaplama, OBS/ağ çağrısı yok
- Yöntem 1'in dayandığı Tablo 1, yönetmelikte açıkça "(Örnektir)" — gerçek T-skor
  sınırlarını ders bazında öğretim üyesi belirler; bu, hem tool description'ında hem
  her sonucun `kaynak_tablo` alanında açıkça belirtiliyor
- Standart sapma sıfırsa (tüm puanlar eşit, T-skoru tanımsız) Yöntem 1 atlanıyor,
  Yöntem 2 yine de hesaplanıyor — hata fırlatmıyor
- `my_score`, `class_scores` listesinde yoksa otomatik ekleniyor (ortalama/STD hesabına
  dahil olması gerektiği için) ve bu açıkça bildiriliyor
- Yeni modül: `src/ninova_mcp/relative_grading.py` (`gpa.py` gibi saf fonksiyonlar)

## v0.6.2

Gerçek bir hesapla canlı doğrulama sırasında bulunan iki `obs_calculate_gpa`/`gpa.py`
düzeltmesi. İkisi de aynı kalıp: bir not/veri sessizce hesaplamadan düşüyordu.

### `LETTER_TO_GRADE`'e ara ("+") notlar eklendi

İTÜ bağıl değerlendirme yönetmeliğinin resmi katsayı tablosunda (Tablo 1) DD+, DC+, CC+,
CB+, BB+, BA+ notları var (1.25/1.75/2.25/2.75/3.25/3.75), ama `gpa.py`'deki tablo bunları
hiç içermiyordu. Sonuç: bu notlardan biriyle geçilen bir ders `calculate_gpa`'da **tamamen
kayboluyordu** — ne ortalamaya girdi, ne `ungraded` listesine düştü, sessizce yok oluyordu.
Gerçek bir hesapta bu, tamamlanan derslerin %25'inden fazlasını (14/55) etkiliyordu.
VF'yle aynı hata sınıfı, ama çok daha sık rastlanan notlarda.

### `obs_calculate_gpa`: harfNotu boş geldiğinde mezuniyet verisine düşüyor

`list_registered_courses`'ın `harfNotu` alanı, test edilen hesapta **her dönemde** (2023'e
kadar giden, kesinlikle notu girilmiş dönemler dahil) `None` dönüyordu — `obs_calculate_gpa`
bu yüzden hangi dönem sorulursa sorulsun hep `gpa: None` veriyordu. Artık boş geldiğinde
`obs_get_graduation_remaining`'in taşıdığı gerçek not, **aynı döneme ait** kayıttan alınıyor
(tekrarlanan bir dersin hangi denemesinin notu sorulduğu döneme ait olduğunu karıştırmamak
için kod-bazlı değil kod+dönem-bazlı eşleştirme). Sonuç `grade_fallback_courses` alanında
bildiriliyor. Test edilen hesapta 9 dönemin 0'ı çalışıyordu, düzeltme sonrası 8'i çalışıyor.

## v0.6.1

Hedefli sağlamlaştırma turu: tool description netleştirme, ölü alias kaldırma, tüm tool
registry'sini tek seferde doğrulayan test, ve taramada bulunan birkaç gerçek düzeltme.
Kırıcı değişiklik yok (tek istisna: `get_courses` alias'ının kaldırılması, aşağıda).

### Netlik

- `get_course_grades`/`obs_get_course_grades` ve `get_course_attendance`/`obs_get_attendance`
  description'ları artık hangisinin Ninova LMS girişi, hangisinin OBS'nin resmî kaydı
  olduğunu karşılıklı belirtiyor
- `obs_get_course_prerequisites` description'ı en başa "Public, no login required." ekledi
  (zaten public bir uç noktaydı, `obs_` öneki authenticated bir tool izlenimi veriyordu)
- `get_courses` kaldırıldı — `list_courses`'ın birebir aynısı tek satırlık bir alias'tı
  (86 → 85 tool)

### Yeni test: `tests/test_tool_registry.py`

Daha önce hiçbir test tüm `TOOLS` listesini tek seferde doğrulamıyordu — her dosya kendi
alt kümesini kontrol ediyordu. Yeni dosya tüm tool'lar için: `inputSchema`'nın geçerli bir
JSON-Schema nesnesi olduğunu, `description`/`title`'ın boş olmadığını, her tool adının
`NinovaMcpApp` üzerinde gerçek bir metoda karşılık geldiğini, ve **şema ile metod imzasının
birebir uyuştuğunu** (parametre adları + zorunluluk durumu) doğruluyor.

### Düzeltmeler

- `obs_download_transcript`, kullanıcı kontrollü `output_dir`'a yerel diske yazdığı için
  `download_resource`/`snapshot_page` gibi araçların yanına, uzak HTTP transport'unun
  dışlama listesine eklendi (yalnızca uzak transport'u ayrıca barındıranları etkiler)
- `tracking-state.json` ve snapshot dosyaları artık atomik yazılıyor (write-then-rename,
  `session_store.py`'nin zaten kullandığı örüntü); yarıda kesilen bir yazma artık dosyayı
  kalıcı olarak bozup her sonraki çağrıyı çökertmiyor
- `NinovaClient` yeniden login'de eski `requests.Session`'ı artık kapatıyor (üç noktada:
  session restore, zorla yeniden login, Playwright fallback) — uzun süre çalışan bir
  process'te (özellikle uzak transport) biriken soket sızıntısını önler
- `NinovaMcpApp`'in lazy client property'lerine kilit eklendi — uzak transport eşzamanlı
  isteklerde aynı client'ı iki kez inşa edip (her biri kendi login'iyle) auth state'i
  bölünmüş bırakabiliyordu
- Rate limiter artık boşalan client bucket'larını periyodik olarak temizliyor (önceden her
  görülen client kalıcı bir dict girdisi bırakıyordu) ve `X-Forwarded-For` header'ı artık
  yalnızca `NINOVA_REMOTE_TRUST_PROXY_HEADERS=1` ile açıkça güvenilir kılınırsa kullanılıyor
  (önceden koşulsuz güveniliyordu — sahte header ile rate limit atlatılabiliyordu)

## v0.6.0

### Prompts (yeni)

MCP istemcilerinde `/` menüsünden seçilen 5 hazır akış eklendi. Her biri tool
zincirini ve sonucu okurken kolayca kaçırılan kuralları içeriyor:

- `weekly_briefing` — yaklaşan teslimler + duyurular; `is_fully_uploaded` ile
  teslim edilmemişleri ayırır
- `plan_next_term` — `plan_remaining_courses` → önşart → kontenjan → çakışma;
  seçmeli slotların neden `unresolved_courses`'a düştüğünü açıklar
- `check_course_eligibility` — `prerequisite_status` / `cross_check` /
  `archive_seasonality` üçünü birden raporlatır; `unknown` ≠ "önşartı yok"
- `research_course` — arşiv geçmişi; `coverage` alanını daima raporlatır
- `gpa_scenario` — mevcut GANO, what-if projeksiyon, hedef ortalama

### Resources (yeni)

Ağ ve kimlik gerektirmeyen iki sabit referans tablosu:

- `itu://reference/grade-scale` — harf notu katsayıları, GANO'ya katılmayan
  notlar, GANO yorum bantları
- `itu://reference/program-types` — `program_type` için geçerli değerler
  (LS/LU/ÖL/LUİ) ve kabul edilen alias'lar

Her ikisi de hem stdio hem uzak HTTP transport'unda kayıtlı.

### Davranış değişikliği: VF notu artık GANO'ya giriyor

`VF` (devamsızlıktan kalma) `gpa.py`'deki katsayı tablosunda hiç yoktu; bu
yüzden VF alınan dersler hesaplamadan **sessizce düşüyor**, kredisi paydaya
girmiyordu ve ortalama olduğundan yüksek çıkıyordu. Artık `VF = 0.00` olarak
tabloda ve `FF` gibi işleniyor:

- VF dersinin kredisi toplam krediye eklenir, katsayısı 0.00 sayılır →
  **hesaplanan GANO düşer** ve OBS'nin resmî rakamına yaklaşır
- VF artık `ff_risk` listesinde "tekrar alınması gerekir" notuyla görünür
- `GE`/`KF`/`IA`/`NA`/`TR`/`MU`/`EK` davranışı değişmedi; bunlar hâlâ
  ortalamaya katılmaz

`LETTER_TO_GRADE` tip anotasyonu da `dict[str, float | None]` olarak düzeltildi
(13 değeri zaten `None` idi).

### Diğer

- CLI: `--list-prompts` ve `--list-resources` bayrakları

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
