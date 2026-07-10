# Advanced

Everything beyond the basic one-click / one-command install: running from source,
configuration variables, the remote HTTP server (for ChatGPT and Claude.ai custom
connectors), Docker, the request model, and the full tool reference.

For basic install, see the [README](../README.md) and the [installation guide](installation.md).

## Requirements

- Python 3.11+
- `requests`, `beautifulsoup4`, `lxml`, `mcp`, `starlette`, `uvicorn`
- Optional: `playwright` for a login fallback when Ninova's form flow changes
  (`pipx install "itu-mcp[playwright]"` then `playwright install chromium`)

The one-click `.mcpb` bundle ships its own Python runtime and all of these, so it
needs nothing preinstalled.

## Configuration

Only two variables are required:

```dotenv
NINOVA_USERNAME=your_username
NINOVA_PASSWORD=your_password
```

The server reads them from the process environment. If a `.env` file exists in the
current working directory (or any parent), `NINOVA_*` variables are loaded from it
without overriding variables already set in the environment.

### Optional overrides

```bash
export NINOVA_BASE_URL="https://ninova.itu.edu.tr"   # default
export NINOVA_STATE_DIR="/absolute/path/to/.ninova_state"
export NINOVA_DISABLE_PLAYWRIGHT_FALLBACK="1"
export NINOVA_ENV_FILE="/absolute/path/to/.env"
export NINOVA_COURSE_CACHE_TTL_SECONDS="60"          # course list cache; 0 disables
export NINOVA_REQUEST_DELAY_MS="120"                 # min gap between HTTP calls
export NINOVA_COMPACT_DEFAULT="0"                    # 1 = shrink heavy tool payloads by default
export NINOVA_SESSION_PERSIST="1"                    # cache cookies under NINOVA_STATE_DIR
export NINOVA_SESSION_MAX_AGE_SECONDS="43200"        # 12h default
export NINOVA_ALLOW_UPLOADS="1"
export NINOVA_OBS_BASE_URL="https://obs.itu.edu.tr"  # OBS student portal
export NINOVA_OBS_JWT_TTL_SECONDS="1500"
```

Username is usually your **İTÜ email** (e.g. `name@itu.edu.tr`), the same value you type on the central login page.

## Run

After installing the package, start the local stdio server:

```bash
itu-mcp
```

It waits silently because your MCP client talks to it over stdin/stdout. To run from a
source checkout without installing:

```bash
PYTHONPATH=src python3 -m ninova_mcp
```

## Remote HTTP server (ChatGPT / Claude.ai custom connectors)

ChatGPT (Developer Mode) and Claude.ai custom connectors cannot use a local stdio
server — they need a publicly reachable **Streamable HTTP** endpoint. Run it only on
infrastructure you control:

```bash
export NINOVA_USERNAME="your_username"
export NINOVA_PASSWORD="your_password"
export NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret"
export NINOVA_REMOTE_API_KEY="$(openssl rand -hex 32)"   # recommended
export NINOVA_REMOTE_REQUIRE_API_KEY="1"                 # refuse start without key
export NINOVA_REMOTE_RATE_LIMIT="60"                     # max MCP requests / window
export NINOVA_REMOTE_RATE_WINDOW_SECONDS="60"
export NINOVA_PUBLIC_BASE_URL="https://itu-mcp.example.com"
export NINOVA_ALLOWED_HOSTS="itu-mcp.example.com"
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
itu-mcp-remote
```

Your connector URL is then `https://itu-mcp.example.com/mcp-choose-a-long-random-secret`.

Endpoints: `GET /healthz` (liveness; no Ninova login by default), `GET /` (status), and
`POST/GET <NINOVA_REMOTE_MCP_PATH>` (MCP). When `NINOVA_REMOTE_API_KEY` is set, MCP
requests must send `Authorization: Bearer <key>` or `X-API-Key: <key>`. OAuth is still
not implemented — treat API key + secret path as the shared secret.

Rate limiting (default 60 req / 60s per client IP) applies to the MCP path. Disable with
`NINOVA_REMOTE_DISABLE_RATE_LIMIT=1` if needed.

> **Privacy warning:** a hosted remote server means whoever uses it sends their İTÜ
> credentials to *your* server. Prefer the local stdio setup for anyone but yourself.

## Docker

```bash
docker build -t itu-mcp .
docker run --rm -p 8000:8000 \
  -e NINOVA_USERNAME="your_username" \
  -e NINOVA_PASSWORD="your_password" \
  -e NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-secret" \
  -e NINOVA_REMOTE_API_KEY="replace-with-long-random-secret" \
  -e NINOVA_REMOTE_REQUIRE_API_KEY="1" \
  -e NINOVA_PUBLIC_BASE_URL="https://your-public-domain.example.com" \
  -e NINOVA_ALLOWED_HOSTS="your-public-domain.example.com" \
  itu-mcp
```

## Request model

Ninova is classic ASP.NET WebForms. This server prefers request-level access to Ninova's
own routes such as `/Sinif/<id>.<id>/Notlar`, `/MesajPanosu`, `/Yoklama`, and
`/UzaktanEgitim`. For interactive actions Ninova uses same-page form `POST` requests with
`__VIEWSTATE`, `__EVENTTARGET`, and related WebForms fields rather than a clean JSON API.

## Exposed tools

- `auth_status` — check whether credentials exist and a fresh Ninova session can be created.
- `refresh_session` — force a new login with the configured credentials.
- `get_dashboard` — read `/Kampus1` and summarize sections, recent items, and courses.
- `list_courses` / `get_courses` — return the discovered courses from the dashboard (TTL-cached; `refresh` bypasses cache).
- `get_course_announcements` — announcements for a course (code, title, path, or URL).
- `get_course_class_files` — structured entries from `Sınıf Dosyaları`, optionally recursive.
- `get_course_lesson_files` — structured entries from `Ders Dosyaları`, optionally recursive.
- `get_course_assignments` — assignments; `include_details` (default true) fetches each detail/upload page.
- `get_course_info` — structured data from `Sınıf Bilgileri`.
- `get_course_sections` — the course routes exposed on the course home page.
- `get_course_grades` — structured data from `Notlar`.
- `get_course_message_board` — topics from `Mesaj Panosu`, optionally with thread details.
- `get_course_attendance` — structured data from `Yoklama`.
- `get_course_remote_learning` — structured data from `Uzaktan Eğitim`.
- `get_course_overview` — combined live or tracked overview of a course (`include_assignment_details` default false).
- `get_dashboard_announcements` — announcements from `/Kampus?1/Duyurular`.
- `get_dashboard_assignments` — assignments from `/Kampus?1/Odevler` (`include_details` default true).
- `sync_all_courses` — snapshot every visible course and detect changes (`include_assignment_details` default false for speed).
- `get_updates` — recently detected changes from the local tracking state.
- `get_upcoming_deadlines` — upcoming assignment deadlines from the tracking state.
- `read_page` — read and structure any Ninova HTML page.
- `crawl_course` — inventory internal pages and downloadable resources inside a course.
- `download_resource` — download a file or page response to disk.
- `read_resource_text` — extract plain text from a PDF/DOCX/TXT (URL or local path) for the LLM.
- `get_assignment_upload_slots` — list file slots / fill status on an assignment upload page.
- `submit_assignment` — upload a **local** file to one slot (requires `confirm=true`; dry-run otherwise).
- `snapshot_page` — save a structured snapshot of a page for later tracking.
- `diff_snapshot` — compare the current page against a stored snapshot.

Many list/overview tools accept `compact=true` to shrink long fields (or set `NINOVA_COMPACT_DEFAULT=1`).

### Assignment upload safety

- `submit_assignment` without `confirm=true` only returns a preview (no network POST of the file).
- Set `NINOVA_ALLOW_UPLOADS=0` to disable uploads entirely.
- Remote HTTP transport does **not** expose upload tools (local stdio only).
- Always verify the target course/assignment/slot with the user before `confirm=true`.

## Notes

- Sessions are created by the normal login flow. With `NINOVA_SESSION_PERSIST=1` (default),
  cookies are saved under `NINOVA_STATE_DIR/session.json` so restarts avoid a full login
  until the session expires (`NINOVA_SESSION_MAX_AGE_SECONDS`, default 12h). The server
  still retries once with a fresh login if Ninova redirects to the login page.
- The course list is cached in process memory (`NINOVA_COURSE_CACHE_TTL_SECONDS`, default 60s)
  so repeated `list_courses` / `_resolve_course` calls do not re-hit the dashboard every time.
- HTTP calls are spaced by `NINOVA_REQUEST_DELAY_MS` (default 120ms) to avoid hammering Ninova
  during bulk syncs.
- When a list parser returns zero items but the page still has content, tools may include a
  `parse_warning` field so the model can tell “empty” from “markup changed”.
- Downloads default to `~/.ninova_state/downloads`; snapshots and tracking state live under
  `~/.ninova_state/` unless `NINOVA_STATE_DIR` is set.
- The remote HTTP entrypoint uses the official Python MCP SDK's Streamable HTTP support.
- CLI: `itu-mcp --version`, `--check-auth`, `--list-tools`, or no args for the stdio server.
- **OBS** tools are prefixed with `obs_`. They reuse the same İTÜ username/password (SSO),
  open `obs.itu.edu.tr/ogrenci/`, fetch a JWT from `/ogrenci/auth/jwt`, and call
  `/api/ogrenci/...` JSON endpoints. Profile/contact tools redact TCKN/phone by default.

### OBS tools

- `obs_auth_status` — JWT / SSO readiness
- `obs_get_profile` / `obs_list_programs` / `obs_list_semesters`
- `obs_get_registration_status` / `obs_get_advisor` / `obs_get_internships` / `obs_get_contacts`
- `obs_list_registered_courses` — registered classes for a semester
- `obs_get_course_grades` / `obs_get_attendance` — per class (`sinifId` or course code)
- `obs_get_schedule` — weekly + final calendar
- `obs_get_graduation_remaining` — remaining courses / debts
- `obs_download_transcript` — save transcript PDF under state dir

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Building the .mcpb bundle

See [installation.md](installation.md#building-the-mcpb-bundle-maintainers).
