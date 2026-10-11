# Changelog

## Yayınlanmamış

### Güvenlik

- **Yazma işlemlerinde kullanıcı onayı:** `submit_assignment` ve `obs_save_registration_draft`
  artık göndermeden hemen önce MCP elicitation ile kullanıcıya doğrudan soruyor. Modelin
  verdiği `confirm=true` bu soruyu atlatamaz. Reddetme, pencereyi kapatma ve geçersiz yanıtta
  hiçbir şey gönderilmez
- Elicitation desteklemeyen istemcilerde eski davranış sürer, sonuçta
  `confirmation.method = "argument_only"` yazar
- Yeni `NINOVA_REQUIRE_ELICITATION=1` ayarı, onay penceresi gösteremeyen istemcilerde yazma
  işlemini reddeder. Varsayılan kapalı. `true`, `yes` ve `on` da kabul edilir, tanınmayan bir
  değer hata verir ve yazma yapılmaz

#### Ağ sınırı

- **Host denetimi gerçekten gönderilen adres üzerinden yapılıyor:**
  `https://baska.example\@ninova.itu.edu.tr/` gibi bir adres `urlparse` için Ninova, `requests`
  için başka bir sunucuydu ve istek o sunucuya gidiyordu. Artık adres `requests`'in hazırladığı
  haliyle doğrulanıyor. Ters eğik çizgi, boşluk, kontrol karakteri ya da `kullanici@` içeren
  adresler ve yönlendirmeler hiçbir istek gönderilmeden reddediliyor
- **Araç argümanı host seçemiyor:** `read_page`, `download_resource`, `read_resource_text`,
  `snapshot_page`, `diff_snapshot`, `crawl_course`, `get_assignment_upload_slots` ve
  `submit_assignment` yalnızca `ninova.itu.edu.tr` adreslerini kabul ediyor. `*.itu.edu.tr` sonek
  eşleşmesi kaldırıldı. Portal, OBS ve yardım masasına yalnızca kendi araçlarının sabit
  adreslerinden erişilir. Giriş zincirine yeni bir İTÜ sunucusu eklenirse
  `NINOVA_EXTRA_SSO_HOSTS` ile tanıtılır
- **Şifre yalnızca giriş sayfasına gidiyor:** giriş formu `girisv3.itu.edu.tr` dışında bir yerde
  görünürse şifre gönderilmiyor, tarayıcıyla giriş de aynı denetimi yapıyor. Yönlendirme, giriş
  formunu ya da yüklenen dosyayı başka bir sunucuya taşıyamıyor
- İTÜ Mail yalnızca `imap.itu.edu.tr:993` adresine bağlanıyor. Başka bir `NINOVA_MAIL_HOST` ya
  da `NINOVA_MAIL_PORT` değeri bağlantı kurulmadan reddediliyor
- Ders arşivi araçları dönem ve branş kodunu istek göndermeden önce biçim olarak doğruluyor

#### Dosya sınırı

- `read_resource_text(path=...)` yalnızca indirme klasörünü (`~/.ninova_state/downloads`)
  okuyor. `download_resource` ve `obs_download_transcript` yalnızca oraya yazıyor.
  `diff_snapshot(snapshot_path=...)` yalnızca anlık görüntü klasörünü okuyor. Oturum dosyası ve
  durum klasörünün diğer dosyaları hiçbir koşulda okunamıyor. Başka klasörler yalnızca
  `NINOVA_EXTRA_READ_DIRS` ve `NINOVA_EXTRA_DOWNLOAD_DIRS` ile açılır
- İndirilen dosyalarda açıldığında kod çalıştıran türlerin (`.exe`, `.bat`, `.ps1`, `.lnk` ve
  benzerleri) adına `.download` ekleniyor, Windows'un ayrılmış aygıt adları reddediliyor, mevcut
  dosyanın üzerine yazılmıyor. İndirme boyutu 200 MiB ile sınırlı (`NINOVA_MAX_DOWNLOAD_MB`)
- Metin çıkarmada 25 MiB üstü dosyalar okunmuyor, PDF'lerde ilk 50 sayfa okunuyor ve sonuç bunu
  belirtiyor. Kapatılmamış `<script>` etiketleri artık işlemi yavaşlatmıyor
- Oturum çerezleri, takip durumu ve kontenjan izleme dosyaları Linux ve macOS'ta `0600`, durum
  klasörü `0700` izniyle yazılıyor. Windows'ta koruma kullanıcı profili klasörünün erişim
  listesinden gelir

#### Ödev yükleme ve hassas bilgi

- Yükleme sayfası Ninova'da ve `/Sinif/<id>.<id>/Odev/<id>/OdevGonder` biçiminde olmalı,
  sayfadaki form da aynı ödevin yükleme sayfasına gönderim yapmalı. Onay penceresi dosyanın
  gerçekten gönderileceği adresi, boyutunu ve SHA-256 özetinin başını gösteriyor. Dosya onaydan
  önce bir kez okunuyor, diskte sonradan değişse bile onaylanan içerik gönderiliyor
- `submit_assignment` ağ yolundaki (`\\sunucu\paylasim`) dosyaları ve durum klasörünün kendi
  dosyalarını (oturum çerezleri gibi) yüklemeyi reddediyor. İndirme klasörü ve bilgisayarındaki
  diğer dosyalar yüklenebilir
- `obs_get_profile`, `obs_get_contacts` ve `get_help_ticket` araçlarında `include_sensitive=true`
  artık tek başına yeterli değil. TC kimlik no, telefon, adres ve adlar yalnızca
  `NINOVA_ALLOW_SENSITIVE_OUTPUT=1` ayarlıysa gizlenmeden döner. Varsayılan kapalı

#### Ayar dosyası

- `.env` artık yalnızca çalışma klasöründe aranıyor, üst klasörler taranmıyor. Başka bir dosya
  için `NINOVA_ENV_FILE` kullan
- Çalışma klasöründe bulunan `.env` güvenlikle ilgili anahtarları ayarlayamıyor: adres ve sunucu
  ayarları, `NINOVA_STATE_DIR`, `NINOVA_OBS_REGISTRATION_WRITES`, ek klasörler, hassas çıktı izni
  ve uzak sunucu ayarlarının çoğu. Yok sayılan anahtarların adları stderr'e yazılır, değerleri
  yazılmaz. `NINOVA_ALLOW_UPLOADS` ve `NINOVA_REQUIRE_ELICITATION` oradan yalnızca sıkılaştırma
  yönünde değişir

#### Uzak HTTP sunucusu

- **Artık varsayılan olarak kapalı:** `itu-mcp-remote`, `127.0.0.1` dışında bir adrese
  bağlanıyorsa `NINOVA_REMOTE_API_KEY` verilmeden başlamaz. Aynı durumda `NINOVA_ALLOWED_HOSTS`
  ya da `NINOVA_PUBLIC_BASE_URL` da zorunludur. Eksik değişken hata mesajında yazar, süreç 2
  koduyla kapanır
- Yerel adreste anahtar verilmezse sunucu her açılışta rastgele bir anahtar üretir ve bir kez
  stderr'e yazar. Host ve Origin denetimi her bağlanma adresinde açık
- Bilerek açık sunucu çalıştırmak isteyenler için iki ayar var, ikisi de her açılışta uyarı
  basar: `NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1` ve `NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1`
- `NINOVA_REMOTE_MCP_PATH` artık boş, `/` ya da `/healthz` olamaz. Bu değerlerle anahtar ve hız
  sınırı devre dışı kalıyordu
- Anahtar ve hız sınırı `GET /` ile `GET /healthz` dışındaki her yola ve her metoda uygulanıyor.
  `NINOVA_REMOTE_PROTECT_ALL` yok sayılıyor
- `GET /` ve `GET /healthz` artık MCP yolunu, sürümü, durum klasörünü ve ayarları döndürmüyor
- Vekil sunucu arkasında istemci adresi `X-Forwarded-For` başlığının sağından okunuyor. Yeni
  `NINOVA_REMOTE_TRUSTED_PROXY_HOPS` (varsayılan 1) güvenilen vekil sayısını belirler
- `submit_assignment` ve `obs_save_registration_draft` uzak sunucuda hiçbir ayarla açılamaz
- Çalışma klasöründeki `.env` içinde yok sayılan bir uzak ayar varsa sunucu başlamaz ve
  anahtarın adını yazar. `NINOVA_REMOTE_API_KEY` de bunlardan biri: o dosyayı yazabilen biri
  anahtarı seçebilirdi
- Docker imajı root olmayan `itumcp` kullanıcısıyla (uid 10001) çalışıyor, durum dosyaları
  `/data` biriminde tutuluyor, taban imaj özet değeriyle sabitlendi

#### Yayın hattı ve bağımlılıklar

- GitHub Actions adımları commit SHA'sına sabitlendi, iş izinleri daraltıldı. `mcp-publisher` ve
  pakete giren CPython çalışma zamanı sabit sürümden indirilip SHA-256 ile doğrulanıyor
- Bağımlılık alt sınırları yükseltildi: `pypdf>=6.14.2`, `starlette>=1.3.1`, `requests>=2.33.0`,
  `mcp>=1.28.1`, `lxml>=6.1.0`
- Depodaki `mcp-publisher` ikili dosyaları ve `scripts/_probe_onsart.py` kaldırıldı

### Yeni araçlar

- **Kontenjan izleme:** `watch_course_sections`, `unwatch_course_sections`,
  `list_watched_course_sections`, `check_watched_course_sections`. İzlenen CRN'ler için herkese
  açık ders programını branş başına tek istekle okur ve son kontrolden bu yana değişeni
  bildirir: dolu şubede yer açıldı, açık şube doldu, kontenjan artırıldı, şube programdan
  kalktı. Giriş gerekmez, en fazla 20 şube izlenir. Sunucu arka planda çalışmaz, kontrol
  yalnızca araç çağrıldığında yapılır. Eksik veri "yer açıldı" sayılmaz, `unknown` olarak döner
- **Takvime aktarma:** `export_course_schedule_ics` ve `export_exam_schedule_ics`. Herkese açık
  ders programından ve final takviminden iCalendar (`.ics`) metni üretir. Derslerin ilk ve son
  günü verilmezse ve akademik takvimde de yoksa araç tarih uydurmaz, `term_dates_required`
  döner. Saati belli olmayan şubeler yazılmaz, `skipped` altında listelenir
- **Yeni MCP kaynakları:** `itu://calendar/academic`, `itu://archive/terms`,
  `itu://campus/buildings` ve üç şablon: `itu://archive/catalog/{course_code}`,
  `itu://archive/grades/{course_code}`, `itu://degree-plan/{program_code}`. Hepsi herkese açık
  veridir. Giriş gerektiren hiçbir veri kaynak olarak sunulmuyor
- **Kayıt haftası istemi:** `registration_week(candidates)`. Aday dersleri çakışma, ön şart,
  geçmiş doluluk ve not dağılımı açısından karşılaştırır. Hiçbir şeyi kaydetmez

### Araç grupları

- Araçlar 12 gruba ayrıldı: `core`, `ninova`, `files`, `obs`, `registration`, `portal`,
  `planning`, `public`, `campus`, `library`, `archive`, `mail`. Her araç tam olarak bir gruba
  ait, grubu olmayan bir araç eklenirse test başarısız olur
- `NINOVA_TOOLSETS` yalnızca seçilen grupları açar, `NINOVA_DISABLED_TOOLSETS` seçilen grupları
  kapatır. İkisi de yoksa davranış eskisiyle aynı: bütün araçlar açık
- `core` grubu (`auth_status`, `refresh_session`, `obs_auth_status`) her zaman açık kalır.
  Bilinmeyen bir grup adı sunucuyu başlatmaz ve geçerli adları listeler
- Aynı seçim uzak sunucuda da geçerli ve yalnızca daraltır
- CLI: yeni `--list-toolsets`, `--list-tools` artık etkin seçimi yazdırır

### İyileştirmeler ve düzeltmeler

- **İlerleme bildirimi:** `sync_all_courses`, `crawl_course`, `obs_get_course_prerequisites`
  ve `obs_get_elective_group` uzun süren işlemlerde adım adım ilerleme gönderiyor
- Ders programı artık `NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS` süresince (varsayılan 60
  saniye) önbellekte tutuluyor. Önceden bu ayar uygulanmıyor, kontenjan sayıları bir saate
  kadar eski kalabiliyordu. Sonuçlara okuma zamanını veren `fetched_at` alanı eklendi
- CI: testler artık Windows'ta da koşuyor (Python 3.11 ve 3.14) ve `ruff check` derlemeyi
  durduruyor

### Yükseltirken bilmen gerekenler

- **Adres alan araçlar** Ninova dışındaki İTÜ adreslerini reddeder. Yardım talepleri için
  `get_help_ticket`, OBS ve Portal için kendi araçlarını kullan
- **`output_dir` ve `path`** artık indirme klasörünün içinde olmalı. Göreli bir ad indirme
  klasörünün alt klasörü sayılır. Başka klasör için `NINOVA_EXTRA_DOWNLOAD_DIRS` ya da
  `NINOVA_EXTRA_READ_DIRS` kullan
- **Üst klasördeki `.env` okunmaz.** Kimlik bilgilerin oradaysa asistanının `env` bloğuna taşı
  ya da `NINOVA_ENV_FILE` ile dosyayı göster
- **Uzak sunucu anahtarı çalışma klasöründeki `.env`'den okunmaz.** `NINOVA_REMOTE_API_KEY`
  değerini ortam değişkeni olarak ya da `NINOVA_ENV_FILE` ile gösterdiğin dosyada ver, yoksa
  sunucu başlamaz
- **Uzak sunucu:** `NINOVA_REMOTE_HOST=0.0.0.0` ile anahtarsız ya da `NINOVA_ALLOWED_HOSTS`
  olmadan çalışan kurulumlar (yalnızca `docker run` ile başlatılanlar dahil) bu sürümde
  başlamaz. Docker'da durum klasörü `/root/.ninova_state` yerine `/data` oldu. İki vekil
  arkasındaki kurulumlarda `NINOVA_REMOTE_TRUSTED_PROXY_HOPS=2` gerekir. `/` ve `/healthz`
  yanıtındaki `mcp_path`, `version` gibi alanları okuyan izleme betikleri güncellenmeli
- `pip install` starlette'i 0.x sürümünden 1.x sürümüne yükseltebilir
- Bir onay penceresi açıkken diğer araç çağrıları bekler: bütün çağrılar tek bir kilitten geçer
- Kontenjan izleme listesi `NINOVA_STATE_DIR` altında `seat-watch-state.json` dosyasında
  tutulur. Uzak sunucuda bu liste sunucuya erişen herkes için ortaktır

## v1.0.0

İlk kararlı sürüm. 0.7.2'den bu yana araç sayısı iki katına yaklaştı, test sayısı 321'den
761'e çıktı ve README baştan yazıldı.

### Yeni araçlar

- **İTÜ Mail (salt okunur):** `mail_status`, `mail_list_inbox`, `mail_get_message`,
  `mail_get_attachment`. Okunmamış durumu değiştirmez, ekleri bellekte inceler
- **Kayıt planlama:** `obs_get_registration_draft`, `obs_get_elective_group`,
  `obs_validate_registration_plan`. CRN listesini çakışma, ön şart, kontenjan ve kredi
  limiti açısından OBS'nin taslak kontrolüyle doğrular
- **Kayıt taslağı kaydetme:** `obs_save_registration_draft`. Varsayılan olarak kapalı,
  yalnızca yerel sunucuda, taslaktan ders silinecekse ayrı onay ister. Gerçek OBS üzerinde
  henüz başarılı bir kayıtla doğrulanmadı
- **Not dağılımı:** `obs_get_grade_distribution` (canlı OBS) ve `archive_grade_distribution`
  (arşiv)
- **Yardım talepleri:** `get_help_ticket` ile talep detayı. Yalnızca kendi taleplerini okur,
  kişi adlarını varsayılan olarak gizler
- **Ders programı:** `get_public_course_schedule` artık ada göre filtre ve sayfalama alıyor
- **Arşiv:** aşağıda anlatılan altı yeni araç

### Düzeltmeler

- OBS ders araması yeniden çalışıyor (eski adres 404 dönüyordu), ön şartlar resmi branş
  tablosundan okunuyor
- Harf notları dönem kimliğiyle, kampüs kartı ve kota Portal'ın JSON servislerinden okunuyor
- Kütüphane varsayılan olarak yeni Sirsi kataloğuna bağlanıyor. Eski hesap araçları yalnızca
  eski sunucu elle ayarlanırsa çalışır
- GANO hesabı eksik notlu dönemlerde uydurma bir sayı yerine boş dönüyor, resmi dönem
  ortalamasıyla karşılaştırma eklendi
- Akademik takvim ve kayıt durumu yeniden yazıldı
- Ninova takibinde eksik okunan sayfalar artık yanlış "silindi" bildirimi üretmiyor
- Windows'ta `tzdata` eksikliği yüzünden çöken takvim, kayıt durumu ve not dağılımı
  araçları düzeltildi
- Yoklama aracı verinin Kepler'den mi Ninova'dan mı geldiğini bildiriyor
- Boş derslik aracı OBS'nin yer tutucu derslik ve bina değerlerini gerçek oda saymıyor
- Ders arşivi yeni adresinden (`itu-ders.com`) okunuyor

### Yükseltirken bilmen gerekenler

- İlk `sync_all_courses` çağrısı takip durumunu yeni biçime çevirir ve o çağrıda fark
  üretmez
- `obs_get_help_tickets` artık varsayılan olarak talep sayfalarını tek tek açmıyor,
  istersen `include_details=true` ver
- `obs_validate_registration_plan` ve `obs_get_elective_group` OBS'ye kontrol isteği
  gönderdiği için istemcilere "salt okunur" olarak bildirilmiyor

Bu sürümdeki araçların ve düzeltmelerin büyük kısmı [tzi4](https://github.com/tzi4)
tarafından katkı olarak eklendi.

### Arşiv: altı yeni salt okunur araç

Arşiv sitesinin yayınladığı ama sunucunun okumadığı veri setleri için araçlar eklendi.
Hepsi kimlik gerektirmez ve mevcut `ItuArchiveClient` üzerinden çalışır (tek host izin
listesi, önbellek, güvenli yönlendirme).

- `archive_grade_distribution(course_code, term?)`: dersin dönem dönem harf notu dağılımı
  (2023-2024'ten itibaren), yayınlanan toplam, yüzdeler ve OBS kaynak adresi. Yalnızca tam
  kod okunur; `BLG 212` ile `BLG 212E` birleştirilmez, kardeş kod `related_codes` altında
  listelenir, arşivin iki kod altında da aynı sayıları tuttuğu dönemler `same_counts_as` ile
  işaretlenir. `+` notları dahil etiketler yayınlandığı gibi kalır, veride olmayan not için
  sıfır uydurulmaz. Yayınlanan toplam ile sayıların toplamı tutmuyorsa ikisi de korunur ve
  `total_mismatch` ile işaretlenir
- `archive_course_catalog(course_code)`: ders adı (TR/EN), dil, krediler, tanım, öğrenme
  çıktıları, haftalık konular, kaynak kitaplar ve kaynak adresi
- `archive_course_unlocks(course_code)`: dersi ön şart olarak anan dersler. Yalnızca bir
  sonraki düzeyi verir; Ve/Veya seçeneklerini ve en düşük notu göstermez, gerçek kural için
  `explain_course_eligibility` aracına yönlendirir
- `archive_search_sections(term?, course_code?, course_name?, instructor?, day?, limit?)`:
  bir dönemin tüm branşlarında tek seferde arama. Türkçe karakterlere duyarlı eşleşme,
  varsayılan olarak arşivin güncel dönemi, toplam eşleşme sayısı ve `truncated` bilgisi
- `archive_exam_schedule(term, branch?, course_code?, limit?)`: dönemin arşive kaydedilmiş
  sınav programı. Dosyası olmayan dönem "sınav yok" olarak değil
  `exam_schedule_not_recorded` olarak döner
- `archive_status`: son tarama, son başarılı tarama, şube sayısı, kısmi tarama bilgisi,
  başarısız branşlar ve gün cinsinden veri yaşı. Yalnızca bilgi amaçlıdır; arşiv kayıt ve
  ekle-bırak haftaları dışında taramayı bilerek durdurduğu için eski tarih uyarı üretmez

Yeni kod `archive_tools.py` içinde; `server.py` yalnızca araçları kaydeden birkaç satır
aldı. URL yoluna giren branş kodu ve dönem kodu, adres kurulmadan önce doğrulanıyor.
9.3 MB'lık `prereq/graph.json` hiçbir araç tarafından indirilmiyor.

## v0.7.2

Gerçek bir kullanım raporundan bulunan OBS oturum kırıklığı düzeltildi:
`plan_next_term` gibi OBS'e giren prompt'lar, `obs_auth_status` "ok: true"
derken bile `obs_get_profile` ham İTÜ giriş sayfası HTML'i döndürüyordu.

Kök neden zinciri:

- `ObsClient.ensure_ready()` (`obs_auth_status`'ın arkasındaki fonksiyon)
  Ninova oturumunu hiç doğrulamıyordu (`ensure_logged_in()` `verify=True`
  olmadan çağrılıyordu) ve JWT'yi yalnızca "hafızada var, TTL dolmamış" diye
  kontrol ediyordu, sunucuya gerçekten geçerli mi diye sormuyordu
- OBS, oturum ölmüşse 401/403 değil, HTTP 200 ile İTÜ giriş sayfasının
  HTML'ini döndürüyor; `api_get` yalnızca `{401, 403}`'ü hata sayıyordu, bu
  yüzden giriş sayfası HTML'i gerçek veriymiş gibi ham metin olarak
  dönüyordu
- `refresh_session`, Ninova'da yeniden login oluyordu ama `ObsClient`'ın
  önbelleğe aldığı eski JWT'yi hiç temizlemiyordu, o yüzden yeniden login
  bile OBS tarafını düzeltmiyordu

Düzeltmeler (`obs_client.py`, `server.py`):

- `ensure_ready()` artık `ensure_logged_in(verify=True)` ile gerçek bir
  doğrulama yapıyor ve JWT'yi zorla tazeliyor, böylece `obs_auth_status`
  artık yanlış "ok" dönmüyor
- Yeni `_looks_like_obs_login_page()`: HTTP 200 + İTÜ giriş sayfası HTML'ini
  tanıyor (content-type ve gövde şekline bakarak)
- `_get_jwt()` artık JWT gibi görünen ama aslında giriş sayfası olan
  yanıtları reddediyor (eski nokta-sayma kontrolü tek başına HTML'i
  kolayca JWT sanabiliyordu)
- `api_get()` giriş sayfası tespit ettiğinde Ninova oturumunu da yeniden
  doğrulayıp JWT'yi tazeliyor, bir kez daha deniyor; hâlâ giriş sayfasıysa
  net bir `ObsError` fırlatıyor (ham HTML dönmek yerine)
- `refresh_session` artık `ObsClient`'ın önbelleğe aldığı JWT'yi de
  temizliyor

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
