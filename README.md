<div align="center">

  <h1>İTÜ MCP</h1>

  <p><em>İTÜ Ninova ve OBS hesabını Claude, Cursor, Codex ve diğer MCP istemcilerine bağla</em></p>

  <p>
    <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/sürüm-v0.2.0-blue?style=flat-square" alt="Sürüm: v0.2.0" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/lisans-MIT-green?style=flat-square" alt="Lisans: MIT" /></a>
    <a href="https://github.com/yatuk/itu-mcp"><img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white&style=flat-square" alt="Python 3.11+" /></a>
    <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-sunucu-black?style=flat-square" alt="MCP Sunucu" /></a>
  </p>

  <br />

  <table>
    <tr>
      <td align="center"><strong>Ninova</strong><br/><code>LMS</code></td>
      <td align="center"><strong>OBS</strong><br/><code>Öğrenci portalı</code></td>
      <td align="center"><strong>MCP</strong><br/><code>Claude · Cursor · Codex</code></td>
    </tr>
    <tr>
      <td align="center">Dersler · dosyalar · ödevler<br/>duyurular · teslim tarihleri</td>
      <td align="center">Kayıt · notlar<br/>transkript · danışman · staj</td>
      <td align="center">Doğal dilde sor<br/>(TR / EN)</td>
    </tr>
  </table>
</div>

<br />

---

## İTÜ MCP nedir?

**İTÜ MCP** bilgisayarında çalışır ve kendi İTÜ hesabını yapay zeka asistanlarına bağlar. Kimlik bilgilerinle (genelde `ad@itu.edu.tr`) giriş yapar, **Ninova** ve **OBS** üzerinden veri okur, [Model Context Protocol](https://modelcontextprotocol.io) üzerinden yapılandırılmış araçlar sunar.

| İhtiyacın | İTÜ MCP cevabı |
|:---|---|
| "Bu hafta hangi ödevlerin teslimi var?" | Ninova ödev ve teslim tarihi araçları |
| "X dersinin notları / yoklaması?" | OBS ara not, harf notu ve yoklama |
| "Transkript / danışman / staj?" | OBS profil, danışman, staj, transkript PDF |
| "PDF özetle" | İndirme + `read_resource_text` (PDF/DOCX) |
| "Ödev yükle" | İsteğe bağlı yükleme, `confirm=true` şart |

> **Önce yerel.** Şifren cihazda kalır; yalnızca İTÜ giriş / Ninova / OBS adreslerine gönderilir. Üçüncü taraf bir sunucuya kimlik bilgisi depolanmaz.
>
> **İTÜ ile resmi bağlantısı yoktur.** Yalnızca kendi hesabınla kullan.

---

## Mimari

```
┌──────────────┐     stdio / HTTP      ┌─────────────────────┐
│ Claude       │ ◄──────────────────► │  itu-mcp            │
│ Cursor       │      MCP araçları    │  (Python 3.11+)     │
│ Codex · …    │                      └──────────┬──────────┘
└──────────────┘                                 │
                                                 │ SSO + JWT
                     ┌───────────────────────────┼───────────────────────────┐
                     ▼                           ▼                           ▼
              ninova.itu.edu.tr           girisv3.itu.edu.tr          obs.itu.edu.tr
                 (LMS HTML)                   (İTÜ giriş)              (JSON API)
```

| Katman | Rol |
|---|---|
| **MCP sunucu** | Araç listesi, sade yanıtlar, CLI (`--check-auth`, `--list-tools`) |
| **Ninova istemcisi** | Oturum + HTML ayrıştırma (duyuru, dosya, ödev, yükleme formu) |
| **OBS istemcisi** | SSO → `/ogrenci/auth/jwt` → `/api/ogrenci/...` |
| **Durum** | İsteğe bağlı çerez önbelleği, izleme anlık görüntüleri, indirmeler (`~/.ninova_state`) |

---

## Hızlı başlangıç

### 1. Kurulum

```bash
pipx install itu-mcp
# veya: pip install --user itu-mcp
# kaynaktan:
#   git clone https://github.com/yatuk/itu-mcp.git
#   cd itu-mcp && pip install -e .
```

### 2. Kimlik bilgileri

```bash
cp .env.example .env
# NINOVA_USERNAME=ad.soyad@itu.edu.tr
# NINOVA_PASSWORD=********
```

Kullanıcı adı genelde **İTÜ e-posta** adresindir; yalnızca yerel kısım değil.

### 3. Duman testi

```bash
itu-mcp --version
itu-mcp --check-auth
itu-mcp --list-tools
```

### 4. MCP istemcisini bağla

**Claude Code**

```bash
claude mcp add itu itu-mcp \
  -e NINOVA_USERNAME=ad.soyad@itu.edu.tr \
  -e NINOVA_PASSWORD=sifren
```

**Codex CLI**

```bash
codex mcp add itu \
  --env NINOVA_USERNAME=ad.soyad@itu.edu.tr \
  --env NINOVA_PASSWORD=sifren \
  -- itu-mcp
```

**Claude Desktop / Cursor:** [docs/installation.md](docs/installation.md) ve `examples/` klasörüne bak.

> **Bitti.** İstemciyi yeniden başlat ve sor: *"Ninova'daki derslerimi listele"* veya *"OBS'te bu dönem kayıtlı derslerim?"*

---

## Ne sorabilirsin?

- *"Bu hafta hangi ödevlerimin teslimi var?"*
- *"EEF 211E sınıf dosyalarındaki PDF'i oku."*
- *"OBS'te 2025-2026 Bahar kayıtlı derslerim neler?"*
- *"CEN 354E ara notlarım?"*
- *"Danışmanım kim? Staj bilgilerimi göster."*
- *"Transkript PDF indir."*

---

## Araç haritası

<table>
  <tr>
    <td align="center" width="50%"><strong>Ninova (LMS)</strong></td>
    <td align="center" width="50%"><strong>OBS (portal)</strong></td>
  </tr>
  <tr>
    <td>
      <code>auth_status</code> · <code>list_courses</code><br/>
      <code>get_course_*</code> · <code>sync_all_courses</code><br/>
      <code>get_upcoming_deadlines</code><br/>
      <code>read_resource_text</code> · <code>submit_assignment</code>
    </td>
    <td>
      <code>obs_auth_status</code> · <code>obs_get_profile</code><br/>
      <code>obs_list_registered_courses</code><br/>
      <code>obs_get_course_grades</code> · <code>obs_get_attendance</code><br/>
      <code>obs_get_advisor</code> · <code>obs_download_transcript</code>
    </td>
  </tr>
</table>

Tam araç listesi, Docker, uzak HTTP, ortam değişkenleri: **[docs/advanced.md](docs/advanced.md)**.

---

## Güvenlik

| Yap | Yapma |
|---|---|
| Yalnızca **kendi** İTÜ hesabını kullan | `.env` veya çerezleri commit etme |
| **Yerel stdio** MCP tercih et | Uzak MCP URL / API anahtarını paylaşma |
| `submit_assignment` yalnızca **`confirm=true`** ile | Önizlemeyi okumadan ödev yükleme |
| Uzak kurulumda `NINOVA_REMOTE_API_KEY` kullan | Gizli path ve anahtar olmadan public açma |

Ayrıntılar: [docs/security.md](docs/security.md).

OBS profil araçları TCKN / telefonu **varsayılan olarak gizler** (`include_sensitive=true` ile açılır).

---

## Yapılandırma (isteğe bağlı)

```bash
export NINOVA_COURSE_CACHE_TTL_SECONDS=60
export NINOVA_REQUEST_DELAY_MS=120
export NINOVA_SESSION_PERSIST=1
export NINOVA_COMPACT_DEFAULT=0
export NINOVA_ALLOW_UPLOADS=1
export NINOVA_OBS_BASE_URL=https://obs.itu.edu.tr
```

`.env.example` ve [docs/advanced.md](docs/advanced.md) dosyalarına bak.

---

## Geliştirme

```bash
git clone https://github.com/yatuk/itu-mcp.git
cd itu-mcp
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[playwright]"
python -m unittest discover -s tests -v
```

---

## Bağlantılar

| Kaynak | URL |
|---|---|
| **Kurulum** | [docs/installation.md](docs/installation.md) |
| **Gelişmiş / araçlar** | [docs/advanced.md](docs/advanced.md) |
| **Güvenlik** | [docs/security.md](docs/security.md) |
| **Değişiklik günlüğü** | [CHANGELOG.md](CHANGELOG.md) |
| **Sorunlar** | [github.com/yatuk/itu-mcp/issues](https://github.com/yatuk/itu-mcp/issues) |

---

## Teşekkür

Bu proje, [**Hikmet Gultekin**](https://github.com/hikmedit) tarafından yazılan orijinal **[ninova-mcp](https://github.com/hikmedit/ninova-mcp)** çalışmasının üzerine kurulmuştur. İlk açık kaynak, kimlik bilgisiyle çalışan İTÜ Ninova MCP sunucusudur (LMS giriş, HTML ayrıştırma, izleme, `.mcpb` paketleme).

İTÜ MCP bu temeli genişletir: OBS öğrenci portalı API'leri, PDF metin okuma, güvenli ödev yükleme, oturum kalıcılığı, uzak API anahtarı ve bu depo altında yeniden paketleme.

---

## Lisans

[MIT](LICENSE). İstanbul Teknik Üniversitesi ile resmi bağlantısı yoktur.

<br />

<div align="center">
  <sub><a href="https://github.com/yatuk">yatuk</a> tarafından · <a href="https://github.com/yatuk/itu-mcp">GitHub</a></sub>
</div>
