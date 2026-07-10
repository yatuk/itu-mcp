<div align="center">

  <h1>İTÜ MCP</h1>

  <p><em>Connect İTÜ Ninova + OBS to Claude, Cursor, Codex &amp; other MCP clients</em></p>

  <p>
    <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/version-v0.2.0-blue?style=flat-square" alt="Version: v0.2.0" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License: MIT" /></a>
    <a href="https://github.com/yatuk/itu-mcp"><img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white&style=flat-square" alt="Python 3.11+" /></a>
    <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-server-black?style=flat-square" alt="MCP Server" /></a>
  </p>

  <br />

  <table>
    <tr>
      <td align="center"><strong>Ninova</strong><br/><code>LMS</code></td>
      <td align="center"><strong>OBS</strong><br/><code>Student portal</code></td>
      <td align="center"><strong>MCP</strong><br/><code>Claude · Cursor · Codex</code></td>
    </tr>
    <tr>
      <td align="center">Courses · files · HW<br/>announcements · deadlines</td>
      <td align="center">Registration · grades<br/>transcript · advisor · internships</td>
      <td align="center">Ask in plain language<br/>(TR / EN)</td>
    </tr>
  </table>
</div>

<br />

---

## What is İTÜ MCP?

**İTÜ MCP** runs on your machine and connects your own İTÜ account to AI assistants. It logs in with your credentials (usually `name@itu.edu.tr`), talks to **Ninova** and **OBS**, and exposes structured tools over the [Model Context Protocol](https://modelcontextprotocol.io).

| Your challenge | İTÜ MCP's answer |
|:---|---|
| "Bu hafta hangi ödevlerin teslimi var?" | Live assignment + deadline tools from Ninova |
| "X dersinin notları / yoklaması?" | OBS midterm/letter grades + attendance APIs |
| "Transkript / danışman / staj?" | OBS profile, advisor, internship, transcript PDF |
| "PDF özetle" | Download + `read_resource_text` (PDF/DOCX) |
| "Ödev yükle" | Optional upload with explicit `confirm=true` |

> **Local-first.** Your password stays on your device and is only sent to İTÜ login / Ninova / OBS — never to a third-party AI backend as credentials storage.
>
> **Not affiliated with İTÜ.** Use only with your own account.

---

## Architecture

```
┌──────────────┐     stdio / HTTP      ┌─────────────────────┐
│ Claude       │ ◄──────────────────► │  itu-mcp            │
│ Cursor       │      MCP tools       │  (Python 3.11+)     │
│ Codex · …    │                      └──────────┬──────────┘
└──────────────┘                                 │
                                                 │ SSO + JWT
                     ┌───────────────────────────┼───────────────────────────┐
                     ▼                           ▼                           ▼
              ninova.itu.edu.tr           girisv3.itu.edu.tr          obs.itu.edu.tr
                 (LMS HTML)                   (İTÜ login)              (JSON APIs)
```

| Layer | Role |
|---|---|
| **MCP server** | Tool registry, compact responses, CLI (`--check-auth`, `--list-tools`) |
| **Ninova client** | Session + HTML parsers (announcements, files, assignments, upload form) |
| **OBS client** | SSO → `/ogrenci/auth/jwt` → `/api/ogrenci/...` |
| **State** | Optional cookie cache, tracking snapshots, downloads under `~/.ninova_state` |

---

## Quick Start

### 1. Install

```bash
pipx install itu-mcp
# or: pip install --user itu-mcp
# from source:
#   git clone https://github.com/yatuk/itu-mcp.git
#   cd itu-mcp && pip install -e .
```

### 2. Credentials

```bash
cp .env.example .env
# NINOVA_USERNAME=your.name@itu.edu.tr
# NINOVA_PASSWORD=********
```

Username is usually your **İTÜ email**, not only the local part.

### 3. Smoke test

```bash
itu-mcp --version
itu-mcp --check-auth
itu-mcp --list-tools
```

### 4. Wire an MCP client

**Claude Code**

```bash
claude mcp add itu itu-mcp \
  -e NINOVA_USERNAME=your.name@itu.edu.tr \
  -e NINOVA_PASSWORD=your_password
```

**Codex CLI**

```bash
codex mcp add itu \
  --env NINOVA_USERNAME=your.name@itu.edu.tr \
  --env NINOVA_PASSWORD=your_password \
  -- itu-mcp
```

**Claude Desktop / Cursor** — see [docs/installation.md](docs/installation.md) and `examples/`.

> **Done.** Restart the client and ask: *"Ninova'daki derslerimi listele"* or *"OBS'te bu dönem kayıtlı derslerim?"*

---

## What you can ask

- *"Bu hafta hangi ödevlerimin teslimi var?"*
- *"EEF 211E sınıf dosyalarındaki PDF'i oku."*
- *"OBS'te 2025-2026 Bahar kayıtlı derslerim neler?"*
- *"CEN 354E ara notlarım?"*
- *"Danışmanım kim? Staj bilgilerimi göster."*
- *"Transkript PDF indir."*

---

## Tool map

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

Full reference, Docker, remote HTTP, env vars: **[docs/advanced.md](docs/advanced.md)**.

---

## Safety

| Do | Don't |
|---|---|
| Use only **your** İTÜ account | Commit `.env` or cookies |
| Prefer **local stdio** MCP | Share remote MCP URL / API key |
| `submit_assignment` only with **`confirm=true`** | Upload without reading the dry-run preview |
| Set `NINOVA_REMOTE_API_KEY` for remote | Expose a public remote without a secret path |

Details: [docs/security.md](docs/security.md).

OBS profile tools **redact TCKN / phone** by default (`include_sensitive=true` to override).

---

## Configuration (optional)

```bash
export NINOVA_COURSE_CACHE_TTL_SECONDS=60
export NINOVA_REQUEST_DELAY_MS=120
export NINOVA_SESSION_PERSIST=1
export NINOVA_COMPACT_DEFAULT=0
export NINOVA_ALLOW_UPLOADS=1
export NINOVA_OBS_BASE_URL=https://obs.itu.edu.tr
```

See `.env.example` and [docs/advanced.md](docs/advanced.md).

---

## Development

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

## Links

| Resource | URL |
|---|---|
| **Installation** | [docs/installation.md](docs/installation.md) |
| **Advanced / tools** | [docs/advanced.md](docs/advanced.md) |
| **Security** | [docs/security.md](docs/security.md) |
| **Changelog** | [CHANGELOG.md](CHANGELOG.md) |
| **Issues** | [github.com/yatuk/itu-mcp/issues](https://github.com/yatuk/itu-mcp/issues) |

---

## Acknowledgments

This project builds on the original **[ninova-mcp](https://github.com/hikmedit/ninova-mcp)** by [**Hikmet Gultekin**](https://github.com/hikmedit) — the first open credential-based MCP server for İTÜ Ninova (LMS login, parsing, tracking, `.mcpb` packaging).

İTÜ MCP extends that foundation with OBS student-portal APIs, PDF text extraction, safer assignment upload, session persistence, remote API-key hardening, and packaging under this repo.

---

## License

[MIT](LICENSE). Not affiliated with İstanbul Technical University.

<br />

<div align="center">
  <sub>Built by <a href="https://github.com/yatuk">yatuk</a> · <a href="https://github.com/yatuk/itu-mcp">GitHub</a></sub>
</div>
