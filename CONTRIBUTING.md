# Katkı rehberi

İTÜ MCP bir öğrenci projesi. Hata bildirmek, bir araç önermek ya da doğrudan kod göndermek için üniversiteden izin almana gerek yok, depoyu çatallaman yeterli.

## Kod yazmadan önce

- Hata bulduysan [sorun kaydı aç](https://github.com/yatuk/itu-mcp/issues/new/choose). `itu-mcp doctor` çıktısını eklersen işimiz kolaylaşır.
- Fikrin ya da sorun varsa [Discussions](https://github.com/yatuk/itu-mcp/discussions) daha uygun.
- Büyük bir değişikliğe başlamadan önce kısa bir sorun kaydıyla haber ver. Aynı işi iki kişi yapmasın, sen de boşa uğraşma.
- Güvenlik açığı bulduysan herkese açık kayıt açma. [Özel bildirim](https://github.com/yatuk/itu-mcp/security/advisories/new) gönder.

Şifreni, öğrenci numaranı, TC kimlik numaranı ve oturum çerezlerini hiçbir yere yazma. Ekran görüntüsü ekliyorsan kişisel bilgileri kapat.

## Geliştirme ortamı

Python 3.11 veya üstü gerekir.

```bash
git clone https://github.com/yatuk/itu-mcp.git
cd itu-mcp
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[playwright,keyring]"
```

Değişikliğini göndermeden önce üçü de temiz geçmeli:

```bash
python -m pytest tests/ -q
python -m ruff check src/ tests/
python -m bandit -r src/ninova_mcp -q
```

Testler ağa çıkmaz ve gerçek hesap istemez. Depo kökünde kendi `.env` dosyan olsa bile testlerin ona ihtiyacı yoktur.

## Dal ve PR

- Çalışmanı `development` dalından ayırdığın bir dalda yap, PR'ı da `development` dalına aç. `main` yalnızca yayınlanmış sürümleri taşır.
- Bir PR tek bir iş yapsın. Hata düzeltmesiyle yeniden düzenlemeyi aynı PR'a koyma.
- Commit mesajları Türkçe ve `tür: ne değişti` biçiminde: `fix: ...`, `feat: ...`, `docs: ...`, `test: ...`, `ci: ...`.
- Kullanıcının göreceği her değişikliği `CHANGELOG.md` içindeki en üst bölüme ekle.

## Kod kuralları

**Sahte veriyle test yaz.** `tests/fixtures/` altındaki sayfalar uydurma kişi ve derslerle hazırlanır. Kendi Ninova ya da OBS sayfanı kaydedip gönderme. Gerçek bir sayfadan örnek gerekiyorsa adları, numaraları, e-postaları ve notları değiştir.

**Testte gerçek sisteme bağlanma.** Gerçek `NinovaClient` ya da `ObsClient` kuran bir test, sahte şifreyle İTÜ'ye giriş dener ve hesabı kilitleyebilir. İstemciyi sahtesiyle değiştir.

**Yeni araç eklerken** dört yere dokunursun: aracın kendisi (`app_*.py` içindeki sınıflardan biri), `tool_metadata/` altındaki açıklaması, `toolsets.py` içindeki grubu ve `docs/advanced.md`. Testler eksik kalan parçayı söyler: her aracın açıklaması olmalı, bir gruba ait olmalı ve yayınlanan şema yöntemin imzasıyla aynı olmalı.

**Yazan araçlar açık onay ister.** Ödev yükleme, kütüphane işlemleri ve kayıt taslağı gibi bir şeyi değiştiren her araç `confirm=true` olmadan çalışmaz ve işletmecinin ayrıca açması gerekir. Yeni bir yazma aracı bu kalıbın dışına çıkmasın.

**Kişisel veriyi azalt.** Araç çıktısına TC kimlik numarası, telefon ya da başka öğrencilerin bilgisi girmesin. Hata mesajlarına şifre, çerez ya da belirteç yazma.

**Türkçe harflere dikkat.** Karşılaştırma yaparken `str.lower()` yetmez: `İ`, `I` ve `ı` ayrı ele alınır. Depodaki katlama yardımcılarını kullan.

**Bilmediğini tahmin etme.** Sayfada bulunmayan bir değer için varsayım döndürme, eksik olduğunu sonuçta belirt. Not ortalaması gibi hesaplarda resmi değerle tutmayan sonucu "tutmuyor" diye işaretle.

## Yeni bir İTÜ sayfası eklemek

Okuduğumuz sayfaların resmi bir API'si yok, biçimleri haber verilmeden değişebilir. Yeni bir sayfa için:

1. Sayfanın yalnızca senin hesabında görünen kısımlarını ayır, örnek dosyayı sahte veriyle yeniden yaz.
2. Ayrıştırıcıyı `parsing_*.py` dosyalarından uygun olanına ekle, beklenmedik biçimde anlaşılır bir hata ver.
3. Sunucuya gereğinden fazla istek atma. Herkese açık sayfalarda önbelleği kullan.

Bursu, yurdu ya da harç borcu olan bir hesabın yoksa o sayfaları biz göremiyoruz. Bu konularda katkı özellikle işe yarar.

## Lisans

Gönderdiğin katkı deponun [lisansı](LICENSE) altında yayınlanır.
