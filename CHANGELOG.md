# Changelog

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
