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

Authenticated Ninova/OBS/Portal tools require two variables. Public OBS, campus,
announcement and library-catalog tools work without them:

```dotenv
NINOVA_USERNAME=your_username
NINOVA_PASSWORD=your_password
# Optional; mail tools fall back to the shared credentials above.
NINOVA_MAIL_USERNAME=your_email
NINOVA_MAIL_PASSWORD=your_mail_password
```

The server reads them from the process environment. It can also load `NINOVA_*`
variables from one `.env` file, without overriding variables already set in the
environment:

- If `NINOVA_ENV_FILE` is set in the process environment, that file is read and no
  other. Every `NINOVA_*` key in it counts, because you named the file yourself.
- Otherwise the `.env` in the current working directory is read, if there is one.
  Parent directories are not searched. So `cp .env.example .env` in a source
  checkout works when the server is started from that directory.

A `.env` that was only found in the working directory may set credentials, cache
lifetimes, pacing, `NINOVA_COMPACT_DEFAULT`, the session cookie settings and the
toolset variables. It may not set the security-relevant keys: `NINOVA_BASE_URL`,
`NINOVA_OBS_BASE_URL`, `NINOVA_LIBRARY_BASE_URL`, `NINOVA_MAIL_HOST`,
`NINOVA_MAIL_PORT`, `NINOVA_LIBRARY_CA_BUNDLE`, `NINOVA_LIBRARY_PROXY_URL`,
`NINOVA_STATE_DIR`, `NINOVA_OBS_REGISTRATION_WRITES` and most remote transport
settings. Two switches can be moved in the stricter direction only:
`NINOVA_ALLOW_UPLOADS=0` and `NINOVA_REQUIRE_ELICITATION=1` are honoured, the
opposite values are not. An ignored key is named on stderr at startup (the value is
never printed). To use such a key, set it in the process environment, for example
in the `env` block of the MCP client, or name the file with `NINOVA_ENV_FILE`.
The reason is in [security.md](security.md#configuration-files).

`ITU_ARCHIVE_*` and `PREREQ_CROSSCHECK_*` are read from the process environment
only. They are never loaded from a `.env` file.

### Optional overrides

```bash
export NINOVA_BASE_URL="https://ninova.itu.edu.tr"   # default
export NINOVA_STATE_DIR="/absolute/path/to/.ninova_state"
export NINOVA_DISABLE_PLAYWRIGHT_FALLBACK="1"
export NINOVA_ENV_FILE="/absolute/path/to/.env"
export NINOVA_COURSE_CACHE_TTL_SECONDS="60"          # course list cache; 0 disables
export NINOVA_TRACKING_MAX_AGE_SECONDS="43200"       # tracking snapshot is stale after this (12h)
export NINOVA_REQUEST_DELAY_MS="120"                 # min gap between HTTP calls
export NINOVA_COMPACT_DEFAULT="0"                    # 1 = shrink heavy tool payloads by default
export NINOVA_SESSION_PERSIST="1"                    # cache cookies under NINOVA_STATE_DIR
export NINOVA_SESSION_MAX_AGE_SECONDS="43200"        # 12h default
export NINOVA_ALLOW_UPLOADS="1"
export NINOVA_REQUIRE_ELICITATION="0"                # 1/true/yes/on = refuse account writes unless the client can ask the user
export NINOVA_ALLOW_SENSITIVE_OUTPUT="0"             # 1 = let include_sensitive=true return unredacted ID, phone, address, names
export NINOVA_EXTRA_READ_DIRS=""                     # extra absolute directories read_resource_text(path=) may read
export NINOVA_EXTRA_DOWNLOAD_DIRS=""                 # extra absolute directories downloads may be written to
export NINOVA_MAX_DOWNLOAD_MB="200"                  # download_resource size limit in MiB
export NINOVA_EXTRA_SSO_HOSTS=""                     # extra exact itu.edu.tr hosts the sign-on redirect chain may pass through
export NINOVA_OBS_BASE_URL="https://obs.itu.edu.tr"  # OBS student portal
export NINOVA_OBS_JWT_TTL_SECONDS="1500"
export NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS="3600"    # public catalog metadata
export NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS="60" # capacity/schedule reads
export NINOVA_ITU_PUBLIC_CACHE_TTL_SECONDS="300"     # directory/SKS/news pages
export NINOVA_LIBRARY_CACHE_TTL_SECONDS="300"
export NINOVA_MAIL_HOST="imap.itu.edu.tr"            # the only accepted host
export NINOVA_MAIL_PORT="993"                        # the only accepted port
export NINOVA_MAIL_TIMEOUT_SECONDS="15"
export ITU_ARCHIVE_BASE_URL="https://itu-ders.com/data"  # course archive
export ITU_ARCHIVE_CACHE_TTL_SECONDS="21600"         # archive regenerates about daily while its scraper runs
export PREREQ_CROSSCHECK_BASE_URL="<community pipe-delimited course feed URL>"
export PREREQ_CROSSCHECK_CACHE_TTL_SECONDS="21600"    # community prerequisite cross-check
```

The separate library patron account does not use the Ninova password:

```dotenv
NINOVA_LIBRARY_NAME=Surname, Name
NINOVA_LIBRARY_ID=student-number
NINOVA_LIBRARY_PIN=separate-library-pin
# Optional custom corporate CA; TLS verification is never disabled automatically.
# NINOVA_LIBRARY_CA_BUNDLE=/absolute/path/to/ca-bundle.pem
```

Username is usually your **İTÜ email** (e.g. `name@itu.edu.tr`), the same value you type on the central login page.

### Toolsets (choosing which tool groups are published)

The server has more than 100 tools, and an MCP client loads every tool's name, description and
schema at the start of each conversation. If you only use part of the server, publish
only the groups you need. Each tool belongs to exactly one group:

| Toolset | Tools | What it covers |
|---|---|---|
| `core` | 3 | Always on: `auth_status`, `refresh_session`, `obs_auth_status` |
| `ninova` | 20 | Ninova courses: dashboard, announcements, files, assignments, grades, upload, change tracking and deadlines |
| `files` | 6 | Read, crawl and download Ninova pages and files, text extraction, snapshots |
| `obs` | 14 | OBS student record: profile, grades, schedule, attendance, transcript |
| `registration` | 5 | OBS registration status, draft, elective groups, plan check |
| `portal` | 6 | Needs login: campus card, cafeteria menu, notifications, help tickets, cloud and mail quota |
| `planning` | 9 | GPA, relative grade, conflicts, degree plan, eligibility |
| `public` | 14 | Public OBS catalog: course search, schedules, prerequisites, exams, calendar, seat watch, calendar export |
| `campus` | 5 | No login: shuttle, directory, buildings, sports hours, announcements |
| `library` | 7 | Catalog search, availability, account, loans |
| `archive` | 15 | Course archive: history, instructors, fill rates, grade distribution |
| `mail` | 4 | Read-only ITU Mail |

```bash
export NINOVA_TOOLSETS="ninova,obs,archive"      # publish only these groups
export NINOVA_DISABLED_TOOLSETS="library,mail"   # or: publish everything except these
```

- With neither variable set, every group is on. That is the default and matches earlier
  versions.
- Both can be set together: `NINOVA_DISABLED_TOOLSETS` is applied after `NINOVA_TOOLSETS`.
- `core` is always on, so `auth_status` is available whatever you select.
  `NINOVA_DISABLED_TOOLSETS=core` is refused.
- Names are comma-separated and case-insensitive. An unknown name stops the server at
  startup with a message that lists the valid names; it is never ignored.
- The variables are read once at startup, from the environment or the `.env` file.
  Restart the server (or the MCP client) after changing them.
- Prompts and resources are not affected. A prompt can mention a tool from a group you
  switched off.

Check the result before starting a client:

```bash
itu-mcp --list-toolsets   # every group with its tool count and on/off state
itu-mcp --list-tools      # the tool names that will be registered
```

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
server. They need a publicly reachable **Streamable HTTP** endpoint. Whoever can call
that endpoint can read the student's grades, schedule, profile and tickets, so run it
only on infrastructure you control:

```bash
export NINOVA_USERNAME="your_username"
export NINOVA_PASSWORD="your_password"
export NINOVA_REMOTE_API_KEY="$(openssl rand -hex 32)"   # required unless the bind is loopback
export NINOVA_PUBLIC_BASE_URL="https://itu-mcp.example.com"
export NINOVA_ALLOWED_HOSTS="itu-mcp.example.com"        # expected Host header values
export NINOVA_ALLOWED_ORIGINS="https://claude.ai,https://claude.com"
export NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-text"   # optional, default /mcp
export NINOVA_REMOTE_HOST="127.0.0.1"                # default; a TLS reverse proxy forwards to it
export NINOVA_REMOTE_RATE_LIMIT="60"                 # max requests per window and client
export NINOVA_REMOTE_RATE_WINDOW_SECONDS="60"
export NINOVA_REMOTE_ENABLE_MAIL="0"                 # explicit opt-in for mail tools
export NINOVA_REMOTE_ALLOWED_TOOLS=""                # optional comma-separated allowlist
export NINOVA_REMOTE_TRUST_PROXY_HEADERS="1"         # only if genuinely behind a proxy
export NINOVA_REMOTE_TRUSTED_PROXY_HOPS="1"          # number of trusted proxies in front
itu-mcp-remote
```

Your connector URL is then `https://itu-mcp.example.com/mcp-choose-a-long-random-text`.
MCP requests must send `Authorization: Bearer <key>` or `X-API-Key: <key>`. OAuth is not
implemented: the API key is the secret. A hard-to-guess path is only an extra, it does
not replace the key.

### What the server refuses to start with

The server fails closed. It prints `error: ...` naming the variable to set and exits
with status 2 in these cases:

| Bind host (`NINOVA_REMOTE_HOST`) | `NINOVA_REMOTE_API_KEY` | `NINOVA_ALLOWED_HOSTS` or `NINOVA_PUBLIC_BASE_URL` | Result |
|---|---|---|---|
| loopback (default `127.0.0.1`, `localhost`, `::1`) | set | either | starts; key required; Host/Origin checked |
| loopback | unset | either | starts with a random one-time key printed to stderr; Host/Origin checked |
| not loopback (`0.0.0.0`, a LAN address, Docker) | set | set | starts; key required; Host/Origin checked |
| not loopback | unset | either | **refuses**: set `NINOVA_REMOTE_API_KEY` |
| not loopback | set | unset | **refuses**: set `NINOVA_ALLOWED_HOSTS` or `NINOVA_PUBLIC_BASE_URL` |

- **Loopback without a key.** Any other process or user on the same machine could
  otherwise call the endpoint. The server therefore generates a random key on each
  start and prints it once to stderr. Copy it into your client for a local test, or set
  `NINOVA_REMOTE_API_KEY` to keep one stable key. The generated key is not stored and
  changes on every start.
- **`NINOVA_REMOTE_REQUIRE_API_KEY=1`** refuses to start without a configured key on
  any bind, loopback included (no key is generated). `NINOVA_REMOTE_REQUIRE_API_KEY=0`
  is the default and is not an opt-out.
- **Host and Origin validation** (DNS-rebinding protection) is always on for the MCP
  path. On a loopback bind with nothing configured, the accepted Host values are
  `127.0.0.1`, `localhost` and `[::1]` with any port. If you set `NINOVA_ALLOWED_HOSTS`
  or `NINOVA_PUBLIC_BASE_URL`, only those values are accepted. A value must match the
  `Host` header exactly, including the port when the client sends one
  (`itu-mcp.example.com:8443`, or `itu-mcp.example.com:*` for any port). A request with
  another Host gets `421`, one with an unlisted `Origin` gets `403`.
- **`NINOVA_REMOTE_MCP_PATH`** must not be empty, `/`, `/healthz` or a path under
  `/healthz`, and may only contain letters, digits and `. _ ~ -` in segments separated
  by single slashes. Anything else is a startup error.

Two opt-outs exist. Each one prints a loud warning on every start. Do not use them on
a server that is reachable from the internet:

| Variable | Effect |
|---|---|
| `NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1` | Start without any API key (no key is generated). Anyone who can reach the port can read the student's data. Ignored when `NINOVA_REMOTE_REQUIRE_API_KEY=1`. |
| `NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1` | Do not check the `Host` and `Origin` headers. Also lifts the allowed-hosts requirement for a non-loopback bind. |

### Endpoints

- `POST/GET/DELETE <NINOVA_REMOTE_MCP_PATH>`: MCP. Needs the key.
- `GET /`: returns `{"name": "itu-mcp", "status": "ok"}`.
- `GET /healthz`: liveness, returns `{"ok": true}` without contacting any university
  system.

`GET` and `HEAD` on exactly `/` and `/healthz` are the only requests served without the
key. Every other path and method, including unknown paths, needs the key and is rate
limited. The two status routes do not reveal the MCP path, the version, the state
directory or any setting. Those are printed to stderr at startup instead.

`NINOVA_HEALTHZ_CHECK_AUTH=1` makes `/healthz` also report whether the stored
credentials can log in (`{"ok": ..., "authenticated": ...}`, status `503` when they
cannot). Because the route is public, the result is cached for
`NINOVA_HEALTHZ_AUTH_CACHE_SECONDS` (default 60, minimum 5), failures included, so
probes cannot trigger more than one login check per interval. The check runs in a worker
thread and never at the same time as a tool call.

### Rate limiting

Default 60 requests per 60 seconds per client address, for everything behind the key.
The two status routes have a separate bucket with the same limits, so health probes and
MCP traffic do not use up each other's budget. Requests with a wrong or missing key
count too. Disable with `NINOVA_REMOTE_DISABLE_RATE_LIMIT=1` if needed. The limiter is
in memory and per process: run a single worker.

The client address is the connecting socket's IP unless
`NINOVA_REMOTE_TRUST_PROXY_HEADERS=1` is set. Turn that on only if a real reverse proxy
sits in front of the server and the server cannot be reached around it. The address is
then taken from `X-Forwarded-For` counting from the right: each proxy appends the address
it received the request from, so the entries on the left are whatever the client sent.
`NINOVA_REMOTE_TRUSTED_PROXY_HOPS` (default `1`) is the number of trusted proxies in
front of the server. With `1` the last entry is used, with `2` the second from last (for
example a CDN in front of your own proxy). If the header is missing, has fewer entries
than that, or the entry is not an IP address, the socket IP is used. Without the
setting, every client behind the same proxy shares one bucket.

`itu-mcp-remote` starts uvicorn with its own proxy-header handling switched off, so
`X-Forwarded-For` is read only by the rule above. If you start the app yourself with
`uvicorn ninova_mcp.remote:app`, pass `--no-proxy-headers` as well.

### Tool selection

The toolset variables above (`NINOVA_TOOLSETS`, `NINOVA_DISABLED_TOOLSETS`) also apply to
the remote server, and they can only narrow its tool list. Tools that are never served
remotely stay excluded, mail tools still need `NINOVA_REMOTE_ENABLE_MAIL=1`, and
`NINOVA_REMOTE_ALLOWED_TOOLS` is applied last: naming a tool from a switched-off toolset
there is a startup error, and if you set it, list `auth_status` yourself to keep it.
`submit_assignment` and `obs_save_registration_draft` are never served over HTTP. No
variable changes that, `NINOVA_OBS_REGISTRATION_WRITES=1` included.

> **Privacy warning:** a hosted remote server means whoever uses it sends their İTÜ
> credentials to *your* server. Prefer the local stdio setup for anyone but yourself.

## Docker

The image listens on `0.0.0.0` inside the container, so the rules for a non-loopback
bind apply: without `NINOVA_REMOTE_API_KEY` and without `NINOVA_ALLOWED_HOSTS` or
`NINOVA_PUBLIC_BASE_URL` the container prints the missing variable and exits with
status 2. It runs as the unprivileged user `itumcp` (uid 10001) and keeps its state
(session cookies, snapshots, tracking state) in the volume `/data`.

```bash
docker build -t itu-mcp .
docker run --rm -p 127.0.0.1:8000:8000 \
  -v itu-mcp-state:/data \
  -e NINOVA_USERNAME="your_username" \
  -e NINOVA_PASSWORD="your_password" \
  -e NINOVA_REMOTE_API_KEY="replace-with-long-random-secret" \
  -e NINOVA_PUBLIC_BASE_URL="https://your-public-domain.example.com" \
  -e NINOVA_ALLOWED_HOSTS="your-public-domain.example.com" \
  -e NINOVA_REMOTE_MCP_PATH="/mcp-choose-a-long-random-text" \
  itu-mcp
```

`-p 127.0.0.1:8000:8000` publishes the port on the host's loopback only, for a TLS
reverse proxy on the same host. If you mount a host directory instead of a named
volume, make it writable for uid 10001. To test the container directly with
`http://localhost:8000`, add `localhost:8000` to `NINOVA_ALLOWED_HOSTS`.

## Request model

Ninova is classic ASP.NET WebForms. This server prefers request-level access to Ninova's
own routes such as `/Sinif/<id>.<id>/Notlar`, `/MesajPanosu`, `/Yoklama`, and
`/UzaktanEgitim`. For interactive actions Ninova uses same-page form `POST` requests with
`__VIEWSTATE`, `__EVENTTARGET`, and related WebForms fields rather than a clean JSON API.

## Exposed tools

- `auth_status` — check whether credentials exist and a fresh Ninova session can be created.
- `mail_status` — verify the read-only IMAPS connection and return inbox counts.
- `mail_list_inbox` — list bounded message headers and UIDs without changing unread state.
- `mail_get_message` — read one bounded text body and attachment metadata with `BODY.PEEK`.
- `mail_get_attachment` — inspect one size-limited PDF, JPEG, or PNG attachment in memory.
- `refresh_session` — force a new login with the configured credentials.
- `get_dashboard` — read `/Kampus1` and summarize sections, recent items, and courses.
- `list_courses` — return the discovered courses from the dashboard (TTL-cached; `refresh` bypasses cache).
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
- `read_page`: read and structure any Ninova HTML page. Like every tool that takes a URL
  or path, it accepts the Ninova host only.
- `crawl_course` — inventory internal pages and downloadable resources inside a course.
- `download_resource`: download a file or page response into the downloads directory.
- `read_resource_text`: extract plain text from a PDF/DOCX/TXT (URL, or a path inside the
  downloads directory) for the LLM.
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
- The upload page must be `https://ninova.itu.edu.tr/Sinif/<id>.<id>/Odev/<id>/OdevGonder`
  (an assignment detail URL is completed to it), and its form must post back to the same
  assignment's upload page. The preview and the result carry `form_url` and
  `file.sha256`. The file is read once, before the confirmation dialog, and those bytes
  are what is sent.

### Local files and URLs

- URL and path arguments (`read_page`, `download_resource`, `read_resource_text`,
  `snapshot_page`, `diff_snapshot`, `crawl_course`, the assignment tools) accept
  `https://ninova.itu.edu.tr/...` or a Ninova path. Other İTÜ hosts are refused; use the
  dedicated OBS, portal and help desk tools for those.
- `download_resource(output_dir=...)` and `obs_download_transcript(output_dir=...)` take
  a directory inside `<NINOVA_STATE_DIR>/downloads` (a relative name is a subdirectory
  of it). To save elsewhere, list the directory in `NINOVA_EXTRA_DOWNLOAD_DIRS`.
- `read_resource_text(path=...)` takes a path inside the downloads directory, or a bare
  file name. To read elsewhere, list the directory in `NINOVA_EXTRA_READ_DIRS`.
- Both variables take absolute directories separated by `;` on Windows and `:`
  elsewhere, for example `NINOVA_EXTRA_READ_DIRS="C:\Users\me\Dersler;D:\Notlar"`.
  The state directory's own files are never readable or writable through these tools.
- A downloaded file whose type runs code when opened gets `.download` appended to its
  name, and the result says so (`filename_neutralised`). Downloads above
  `NINOVA_MAX_DOWNLOAD_MB` (default 200) are refused.
- `include_sensitive=true` on `obs_get_profile`, `obs_get_contacts` and
  `get_help_ticket` returns unredacted data only when `NINOVA_ALLOW_SENSITIVE_OUTPUT=1`
  is set. Without it the result stays redacted and carries
  `sensitive_output: "disabled_by_operator"`.
- If login starts failing with "URL is not allowed" for an `itu.edu.tr` host, İTÜ has
  added a host to the sign-on redirect chain. Add that exact host to
  `NINOVA_EXTRA_SSO_HOSTS` (comma separated).

### Confirmation dialogs (elicitation)

`submit_assignment` and `obs_save_registration_draft` ask the user to confirm through the
MCP client itself, just before anything is sent. This is in addition to `confirm` and
`allow_replace`, which stay required.

- Client supports elicitation: a Turkish dialog lists exactly what will be uploaded or
  saved. The result contains `confirmation.method = "elicitation"`. If the user declines
  or closes the dialog, nothing is sent and the result has
  `status: declined_by_user` or `cancelled_by_user`.
- Client does not support elicitation: behaviour is unchanged and the result contains
  `confirmation.method = "argument_only"`.
- `NINOVA_REQUIRE_ELICITATION=1` (also `true`, `yes`, `on`): clients without elicitation
  are refused with `status: elicitation_required`. Default is off. An unrecognised
  value is an error and no write goes through.

Other tool calls on the same server wait while a dialog is open, because tool calls are
handled one at a time.

### Progress notifications

Four tools make many requests and can run for tens of seconds: `sync_all_courses`,
`crawl_course`, `obs_get_course_prerequisites` (the postrequisite scan reads every branch
table on a cold cache) and `obs_get_elective_group` (spaced OBS checks). When the client
sends a `progressToken` with the call, they emit `notifications/progress` while they
run, with a short Turkish message naming the current step:

| Tool | `progress` / `total` |
| --- | --- |
| `sync_all_courses` | courses finished / courses to sync |
| `obs_get_course_prerequisites` | branch tables read / branches to scan |
| `obs_get_elective_group` | two read steps, then one step per checked section |
| `crawl_course` | pages read, no total (the page count is not known in advance) |

Without a progress token nothing is sent. These six tools run in a worker thread so the
notifications and the dialog answer can travel while the tool is still working; all
other tools run as before. On the remote HTTP transport the server answers each call
with a single JSON response, so progress only reaches a client that keeps the optional
server-to-client stream open.

### Mail read-only scope

Mail tools select only `INBOX` with `readonly=True` and fetch content with `BODY.PEEK`.
They cannot send, delete, move, flag, or mark messages as read. Attachment inspection accepts
only bounded PDF, JPEG, and PNG data and never executes files. Remote HTTP keeps all mail tools
disabled unless `NINOVA_REMOTE_ENABLE_MAIL=1`; `NINOVA_REMOTE_ALLOWED_TOOLS` can further narrow
the published tool set.

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
- Downloads go to `~/.ninova_state/downloads`; snapshots and tracking state live under
  `~/.ninova_state/` unless `NINOVA_STATE_DIR` is set. Other download or read
  directories need `NINOVA_EXTRA_DOWNLOAD_DIRS` / `NINOVA_EXTRA_READ_DIRS`. Session,
  tracking and seat-watch files are written owner-only on Linux and macOS; see
  [security.md](security.md#data-stored-locally) for what applies on Windows.
- In-memory caches keep at most 2048 entries each. Past that, expired entries are
  dropped first and then the oldest ones.
- The remote HTTP entrypoint uses the official Python MCP SDK's Streamable HTTP support.
- CLI: `itu-mcp --version`, `--check-auth`, `--list-tools`, or no args for the stdio server.
- **OBS** tools are prefixed with `obs_`. They reuse the same İTÜ username/password (SSO),
  open `obs.itu.edu.tr/ogrenci/`, fetch a JWT from `/ogrenci/auth/jwt`, and call
  `/api/ogrenci/...` JSON endpoints. Profile/contact tools redact ID numbers, e-mail, phone, address and birth details by default
  and list the hidden fields in `redacted_fields`;
  `include_sensitive=true` needs `NINOVA_ALLOW_SENSITIVE_OUTPUT=1`.

### OBS tools

- `obs_auth_status` — JWT / SSO readiness
- `obs_get_profile` / `obs_list_programs` / `obs_list_semesters`
- `obs_get_registration_status` / `obs_get_advisor` / `obs_get_internships` / `obs_get_contacts`
- `obs_list_registered_courses` — registered classes for a semester
- `obs_get_course_grades` / `obs_get_attendance` — per class (`sinifId` or course code)
- `obs_get_schedule` — weekly + final calendar
- `obs_get_graduation_remaining` — remaining courses / debts
- `obs_download_transcript`: save transcript PDF under the downloads directory (`obs/` by
  default); it writes to disk, so like `download_resource`/`snapshot_page` it is not exposed
  by the remote HTTP transport

### Portal and derived planning tools

- `obs_get_campus_card` — balance and recent campus-card movements
- `get_cafeteria_menu(date, meal, vegan)` — dated menu, calories and allergens
- `obs_get_notifications(notification_id, limit)` — list/detail when Portal API permits;
  falls back to the server-rendered widget when the Portal WebMethod is unavailable
- `obs_get_help_tickets(query, limit, include_details=false)`: search the Portal list by
  ticket ID, title or status. Each row returns `id`, `title`, `unit`, `category`,
  `status`, `created_at`, `updated_at`, `url` and `has_reply`. `total_matching_count`
  is measured before the limit. The Portal list alone leaves unit, category and dates
  `null`; they are listed in `missing_fields`. Set `include_details=true` to fill them
  from the help desk, which opens one ticket page per selected row. A failed detail
  read keeps the Portal row and adds `detail_warning`. Relative `age` is not an exact
  creation date.
- `get_help_ticket(ticket_id, include_sensitive=false)`: read one of your own tickets
  from the authenticated help desk, including its description, subcategory, operation
  history, institutional replies and attachment links. Only IDs that appear in your own
  Portal ticket list are read; any other ID is refused before a request is made.
  Names of the requester and of staff are replaced with `[requester]` and `[staff]`,
  in author fields and in message text, unless `include_sensitive=true`. Attachments
  are listed without downloading. `has_reply` compares operation authors with the
  original requester and is `null` when history or authors are unavailable. Messages
  are in chronological order. The help desk writes slash-separated history dates as
  month/day/year (checked against a live ticket). Timestamps use the source's local
  time without assuming a timezone. Detail reads share the course-cache TTL
  (60 seconds by default).
- `obs_get_cloud_quota`
- `obs_simulate_cumulative_gpa(projected_grades, default_grade, extra_courses, semester)`: projects the cumulative GPA from the student's own record; a retake replaces the earlier grade; read-only estimate
- `obs_calculate_gpa(projected_grades)` — projected values override existing grades
- `calculate_target_gpa` — aggregate target-GPA estimate without reading OBS
- `estimate_relative_grade(class_scores, my_score)` — likely letter grade under İTÜ's
  bağıl değerlendirme rules, by both official methods (T-score against an example
  class-level table, and mean ± standard-deviation multiples); pure local computation,
  no OBS call. The example table is exactly that — an example — so treat the result as
  a planning estimate, not the official grade. Scores must be finite numbers from 0 to 100
  (a bad value is rejected and named). No letter is estimated when fewer than 10 scores
  are counted or when every score is equal: the result has `estimate_available: false`,
  `no_estimate_reason` (`class_too_small` or `zero_std_dev`) and still carries the mean
  and standard deviation. `my_score` is added to the list unless the same value is
  already in it; `my_score_included` in the result says which case applied
- `check_course_conflicts` — scans one or more department codes
- `get_personal_exam_calendar` — authenticated student's official final calendar

### Public OBS and campus tools (no credentials)

- `obs_search_courses` / `obs_get_course_prerequisites`
- `get_public_course_schedule` / `get_public_course_prerequisites`
  - Schedule queries accept an optional course name/code fragment, such as
    `query="Differential Equations"` or `query="MAT201E"`. Matching ignores case,
    accents and code spacing, and includes full sections. Filtering happens in
    the MCP server after reading the official department schedule.
  - Filtered or paged calls return 50 sections by default, with `limit` from 1 to
    100 and a non-negative `offset`. Results include `matched_count`,
    `total_course_count`, and `next_offset`. Continue with the same department,
    query, CRN and limit. Pages reflect the current schedule and may change if
    OBS updates it between calls. A CRN and query together select their
    intersection. Calls without the new options retain the full schedule.
- `get_public_exam_schedule(department_code)` — official current final timetable
- `get_academic_calendar(date_from, date_to, category, query)`: the whole current academic
  year in Turkish and English (`description_tr`, `description`), with `terms` giving the
  first and last day of classes per term. A query made only of exam words (`final`,
  `sınav`, `bütünleme`) leaves out registration rows that contain the same word, such as
  "Final Registration"; the result says so in `query_note`. With only one of `date_from`
  and `date_to`, `coverage_warning` appears only when that date lies outside `coverage`
- `list_degree_faculties` → `list_degree_programs` → `build_degree_plan`
- `find_open_course_sections(department_codes, program_type?, min_available_seats?, query?, program_code?)`:
  sections with seats the student can take, across up to 25 selected departments. OBS
  reserves part of a section's capacity for named programmes (`PROGRAM/quota/enrolled`
  segments, "Diğer" is the general pool), so `available_seats` counts the general pool
  only, or the programme's own remaining quota plus the general pool when `program_code`
  (`CEN_LS` or `CEN`) is given. `total_free_seats` is capacity minus enrolled and
  `reserved_free_seats` lists what is held for other programmes. Sections without a day
  and time are listed last (`unscheduled: true`), "Ek Sınav" rows are left out, and an
  empty result has `no_result_reason` (`query_matched_no_section`,
  `matching_sections_full`, `free_seats_reserved_for_other_programs`, ...). `query`
  ignores case, accents and spaces in the code (`mat210e` finds `MAT 210E`)
- `find_empty_classrooms(department_codes, day, time, program_type?, building?)`:
  coverage-limited estimate from selected schedules; does not include room reservations.
  `day` takes Turkish or English names and abbreviations (`Salı`, `sali`, `Sal`,
  `Tuesday`, `Tue`), `time` takes `10:00`, `9:45`, `10.00`, `0945` or `10`
- Schedule rows: cells the OBS page leaves empty (`-`, `--`, `-/-`, `undeclared`) are
  `null`. A section with no day and time has `unscheduled: true`, one without an announced
  building or room `location_announced_later: true`. `reservation` is the page's text and
  `reservations` its parsed segments; `section_kind` is `extra_exam` for "Ek Sınav" rows
- Seat watch (local state, no login; see [tracking.md](tracking.md#seat-watch)). The server
  does not run in the background and sends no notifications: seats are read only when the
  check tool is called, by the user or by a scheduled task on the client
  - `watch_course_sections(crns, department_code?, program_type?)`: adds up to 12 CRNs per
    call to a watch list of at most 20 sections. Without `department_code` the branch is found
    from the archive's current-term search index and confirmed against the live schedule
  - `unwatch_course_sections(crns? | stale_only? | clear_all?)`: removes the named CRNs, the
    entries left from a past term, or the whole list
  - `list_watched_course_sections`: the stored list and last observation, without reading OBS
  - `check_watched_course_sections`: one schedule request per watched branch. Reports capacity,
    enrolled, seats free and `changes` (`opened`, `filled`, `capacity_raised`, `delisted`, ...)
    per section. `unknown` is never a free seat and `not_listed` is not the same as full.
    `freshness.all_from_cache` says the numbers were not re-read, with `retry_after_seconds`
- `explain_course_eligibility` — evaluates the official OBS branch prerequisite table
  (`/public/GenelTanimlamalar/OnsartAra`): full Ve/Veya expression, per-course minimum
  grades, and credit requirement. Accepts 3- and 4-digit codes (`CEN 4901E`) and
  two-letter suffixes (`FIZ 101EL`). `prerequisite_status` separates `no_prerequisites`
  (proven absent from the official table) from `unknown` (table unreadable), so an empty
  result is never ambiguous. OBS remains authoritative. The result also carries
  `cross_check`, an independent diff against a third-party community dataset (unofficial);
  a disagreement is reported, never resolved silently in either direction, and a failed or
  missing lookup reports `available: false` without affecting the OBS-derived answer.
- `search_itu_directory` — uses the official CSRF-protected directory form
- `search_campus_locations`: official OBS building codes and names (not coordinates).
  `locations` are buildings, `sub_locations` block or room codes inside a building (the
  source lists `DZB` only that way), `pseudo_entries` rows that are not places (online,
  to be announced). The source covers teaching buildings and sports areas only
- `get_shuttle_schedule` / `get_sports_facility_hours`. Sports hours are `HH:MM` with
  `closed: true` for a period the facility does not open, and a closure written into the
  facility name on the page ("(Pazartesi Kapalı)") is returned as `closed_days`
- `get_itu_announcements`: İTÜ, ÖDEK, İKM, SKS and Erasmus aggregation, newest first across
  sources by `published_at_iso` (undated last). `empty_sources` names sources that answered
  with no items, `source_errors` those that failed. When `limit` is smaller than what the
  sources offer, each source first gets an equal share
- `export_course_schedule_ics(crns, department_codes, program_type?, term_start?, term_end?)`:
  the weekly meetings of the given CRNs as iCalendar (RFC 5545) text, read from the public
  course schedule. Each meeting is a weekly recurring event between the first and last day
  of classes. `term_start` and `term_end` (`YYYY-MM-DD`) are optional: when one is omitted
  the tool takes it from the `terms` list of the academic calendar, which is read for the
  whole academic year and states the first and last day of classes for the same academic
  year and level. Only when the calendar does not state a date (a term the site no longer
  or does not yet list) does the call return `status: "term_dates_required"`, `ics: null`
  and the names of the missing parameters. No date is ever guessed. A meeting without a day
  or time (an OBS placeholder such as `-`, `--`, `-/-`) is listed in `skipped` with a reason and gets no
  event; CRNs not found in the scanned departments are in `missing_crns`. Holidays and
  make-up classes are not removed from the recurrence
- `export_exam_schedule_ics(course_codes)`: the current public final exam schedule, filtered
  to the given course codes, as single dated events. Rows with an unreadable date or time
  are in `skipped`, codes without a row in `not_found`, and a branch whose schedule is not
  published yet is skipped with `exam_schedule_not_published`. When only a start time is
  published the event has no end time and the course is named in `end_time_unknown`

Both export tools return the calendar in `ics` together with `event_count`, `summary`,
`suggested_filename` and `mime_type: "text/calendar"`. They return text only and never write
a file, so unlike `download_resource` and `obs_download_transcript` they need no
`output_dir` and are available on the remote transport; save the text as a UTF-8 `.ics`
file on the client side. Times are Europe/Istanbul local times with a fixed UTC+3
`VTIMEZONE` block written by the server itself (no dependency on the system timezone
database). UIDs are derived from term, CRN, weekday and time (exams: CRN, code, date and
time), so importing the same export again updates events instead of duplicating them.
Lines end in CRLF, are folded at 75 octets without splitting a multi-byte character, and
commas, semicolons, backslashes and line breaks in text fields are escaped.

All public HTTP clients use exact HTTPS host allowlists and the shared
`NINOVA_REQUEST_DELAY_MS` throttle. Public clients do not receive SSO cookies.

### Archive tools (no credentials)

Backed by [itu-archive](https://github.com/yatuk/itu-archive), which preserves every term
from 2016-2017 onward. OBS publishes only the active term, so these answer what OBS cannot.

- `archive_list_terms` — archived terms, their source, and known gaps
- `archive_who_taught(course_code)` — instructors per course, ranked by terms taught
- `archive_course_history(course_code)` — term-by-term sections, instructors, and seasonality
- `archive_fill_rate(crn | course_code)` — quota time series for a CRN, or historical fill
  ratios across a course's past sections
- `archive_instructor_courses(instructor)` — an instructor's course history
- `archive_term_sections(term, branch | course_code)` — sections for a term, including terms
  OBS has not published yet
- `archive_search_courses(query)` — search the full archive course index by code or name
  fragment, across every term ever seen (`obs_search_courses` only covers the active term)
- `archive_list_branches(term)` — every branch present in one term's dump, with section/course
  counts; answers "does this branch even have a döküm for this term?" directly
- `archive_compare_terms(course_code, term_a, term_b)` — diffs one course's sections between
  two terms: instructor turnover, section-count delta, capacity/fill movement
- `plan_remaining_courses(program_id?)` — combines `obs_get_graduation_remaining`'s remaining
  required courses with archive seasonality and `archive_who_taught` history, producing one
  scheduling recommendation per course (e.g. "only offered in Güz, usually taught by X,
  average fill 0.95") instead of requiring a manual `archive_course_history` call per course
- `archive_grade_distribution(course_code, term?)`: letter grade counts, published total and
  percentages per term (from 2023-2024 on), with the OBS source URL. Only the exact code is
  read: `BLG 212` and `BLG 212E` are never merged, the sibling is listed under `related_codes`,
  and `same_counts_as` marks terms where the archive holds identical numbers under both codes.
  Grade labels are passed through as published (including the `+` grades) and a label missing
  from a term is not zero-filled. `total_mismatch` flags a published total that differs from
  the sum of the counts; both numbers are kept
- `archive_course_catalog(course_code)`: names, language, credits, description, learning
  outcomes, weekly topics, textbooks and source URL. Fields the catalog lacks are named in
  `missing_fields`
- `archive_course_unlocks(course_code)`: courses that list the course as a prerequisite. Direct
  next level only. Prerequisite rules have AND/OR alternatives and minimum grades that this
  flat list does not express, so use `explain_course_eligibility` for the real rule
- `archive_search_sections(term?, course_code?, course_name?, instructor?, day?, limit?)`:
  searches one whole term across all branches. Turkish-aware matching ("sahin" finds "Şahin"),
  defaults to the archive's current term, reports `match_count` and `truncated`
- `archive_exam_schedule(term, branch?, course_code?, limit?)`: the exam schedule recorded for
  a term. Most terms have none, and that comes back as `exam_schedule_not_recorded`, not as an
  empty schedule
- `archive_status`: last run, last success, section count, partial flag, failed branches and
  data age in days. Informational only: the archive stops scraping outside registration and
  add/drop weeks, so an old date is expected

Every result carries a `coverage` field. An empty result means one of three different things
— the term was never captured (`term_missing`), the branch is absent from that term's dump
(`branch_absent_from_term`), or the filters matched nothing (`covered`) — and the tool always
says which. Quota data is not live: `archive_fill_rate` reports the age of the last snapshot and whether the archive is scanning (it pauses outside registration and add/drop weeks); check OBS before registering.

The dataset tools above use the same field with their own values, so "the archive never
recorded this file" stays distinct from "nothing matched": `branch_grades_missing`,
`course_absent_from_grades`, `term_absent_for_course`, `branch_catalog_missing`,
`course_absent_from_catalog`, `not_listed_as_prerequisite`, `search_index_missing` and
`exam_schedule_not_recorded`. They never download `prereq/graph.json` (several MB); only the
small reverse index is read. Branch codes and term slugs are validated before a URL is built.

`explain_course_eligibility` also carries `archive_seasonality` when the archive has the
course: a course can be prerequisite-eligible right now and still only ever open in one term
a year, and this surfaces that without a separate lookup.

The archive client uses its own cookie-free session and a single-host HTTPS allowlist derived
from `ITU_ARCHIVE_BASE_URL`.

### Prompts

User-invoked templates (the `/` menu in Claude Desktop). They are not triggered by the
model on its own — each one names a tool chain and restates the rules that are easy to
misread in these tools' output.

- `weekly_briefing(days="14")` — upcoming deadlines + recent activity; splits on
  `is_fully_uploaded`
- `plan_next_term()` — `plan_remaining_courses` → eligibility → open sections → conflicts
- `check_course_eligibility(course_code)` — forces `prerequisite_status`, `cross_check`
  and `archive_seasonality` to be reported together
- `research_course(course)` — archive history; forces `coverage` to be reported
- `gpa_scenario(target_gpa="", projected="")` — current GPA, what-if, target average
- `registration_week(candidates)`: compares candidate course codes and CRNs (one
  comma-separated string) for registration week. Sections, then `check_course_conflicts`,
  `explain_course_eligibility`, `archive_fill_rate` and `archive_grade_distribution`, then a
  recommendation with the open risks. It restates that `missing_crns` means "not checked",
  that eligibility `unknown` is not "no prerequisite", that archive `coverage` values such
  as `branch_grades_missing` mean "not recorded", and that validator status `incomplete` is
  not an approval. `obs_validate_registration_plan` and `obs_get_elective_group` send a
  check to OBS that counts against a quota, so the prompt asks for one deliberate call
  after the list is narrowed, never one per candidate. Nothing is saved or registered by
  this flow, and it tells the model not to call `obs_save_registration_draft`

Prompt arguments arrive as strings over the wire regardless of annotation, so every
builder parses its own input and falls back to a default on garbage.

### Resources

Resources are context a user or client attaches on purpose, as opposed to tools the model
calls. Two kinds are exposed, and none of them needs a login.

Static reference tables (no network, no per-student state):

- `itu://reference/grade-scale` — letter-grade coefficients, grades excluded from the
  GPA, failing grades, and the GPA comment bands
- `itu://reference/program-types` — valid `program_type` values (LS/LU/ÖL/LUİ) and their
  accepted aliases, so callers stop discovering them by triggering an error

Public data, fetched through the same clients the tools use (host allowlists, safe
redirects, throttle and TTL caches all apply):

- `itu://calendar/academic`: the academic calendar as `get_academic_calendar` returns it
  with no argument: the whole current academic year is read, `terms` gives the first and
  last day of classes, and the events listed are the recent and upcoming ones
  (`default_window`); check `coverage`
- `itu://archive/terms`: the terms the course archive holds, the current term slug and the
  known gaps
- `itu://campus/buildings`: official OBS building codes and names (`locations`), with
  in-building codes under `sub_locations` and non-places under `pseudo_entries`

Resource templates:

- `itu://archive/catalog/{course_code}`: the archive catalog entry of a course, the same
  payload as `archive_course_catalog`
- `itu://archive/grades/{course_code}`: the historical letter grade distribution of a
  course, the same payload as `archive_grade_distribution` over every recorded term
- `itu://degree-plan/{program_code}`: the latest official undergraduate degree plan of an
  OBS program code such as `BLGE_LS`, with older versions under `available_plans`. The
  public form is keyed by faculty, so the faculty is found by reading each faculty's
  program list (cached for an hour); the first read of a session is therefore slower.
  Courses are listed once, under `plan.semesters[].courses`; `ects` is a number, or `null`
  with `ects_min`/`ects_max`/`ects_raw` when the plan gives a range

Write the course code without a space (`itu://archive/catalog/BLG223E`); a percent-encoded
space and lower case are accepted too. Every parameter is percent-decoded and validated
before a URL is built: course codes with the validator the archive tools use, program codes
against a strict pattern and then against the official program lists. A malformed parameter
is rejected without any request. A well-formed but unknown course code is an answer, not an
error: the payload carries `coverage` (`course_absent_from_catalog`, `branch_grades_missing`
and so on). An unknown program code is an error with a one-line message. Error messages
never include a stack trace.

Each read returns `application/json` with `resource_uri`, `source_url`,
`untrusted_external_content: true` and a `content_notice`, like the tool results.

Nothing that needs the student's session (grades, transcript, personal schedule, mail) is a
resource: a client may read resources without a per-call approval prompt, so this surface
is limited to public data. All resources are registered on both the stdio and remote HTTP
transports, and `itu-mcp --list-resources` prints the URIs and the templates.

### Library tools

- `library_search` / `library_get_item` / `library_check_availability` are public.
  The default Sirsi adapter uses `SD_ILS:` record IDs and paginates using `next_offset`.
  Copy statuses that require asynchronous loading remain unknown.
- `library_get_account` / `library_list_loans` are unavailable on the current Sirsi
  platform. Separate library credentials apply only to explicit legacy configuration.
- `library_renew_loan` and `library_reserve_item` are unavailable on Sirsi. In legacy
  mode they are dry-run by default and submit only with `confirm=true`. They are not
  exposed by the remote HTTP transport.
- If the library host presents an expired or otherwise invalid certificate, the client
  fails closed with a TLS error instead of disabling verification.

See [catalog support and live transport limits](library-catalog.md) for acceptance scope.

### External-content safety

Tool results are marked with `untrusted_external_content` and a `content_notice`.
Announcements, assignment descriptions, catalog metadata and other fetched text are data;
an MCP client must not execute instructions embedded in that text. Sanitization strips HTML
where practical, but provenance marking—not keyword removal—is the primary prompt-injection
control.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Building the .mcpb bundle

See [installation.md](installation.md#building-the-mcpb-bundle-maintainers).
