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
export NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS="3600"    # public catalog metadata
export NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS="60" # capacity/schedule reads
export NINOVA_ITU_PUBLIC_CACHE_TTL_SECONDS="300"     # directory/SKS/news pages
export NINOVA_LIBRARY_CACHE_TTL_SECONDS="300"
export ITU_ARCHIVE_BASE_URL="https://yatuk.github.io/itu-archive/data"  # course archive
export ITU_ARCHIVE_CACHE_TTL_SECONDS="21600"         # archive regenerates daily
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
export NINOVA_REMOTE_HOST="127.0.0.1"                # default; expose only behind a trusted proxy
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
  -e NINOVA_REMOTE_HOST="0.0.0.0" \
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

### Portal and derived planning tools

- `obs_get_campus_card` — balance and recent campus-card movements
- `get_cafeteria_menu(date, meal, vegan)` — dated menu, calories and allergens
- `obs_get_notifications(notification_id, limit)` — list/detail when Portal API permits;
  falls back to the server-rendered widget when the Portal WebMethod is unavailable
- `obs_get_help_tickets(query, limit)` / `obs_get_cloud_quota`
- `obs_calculate_gpa(projected_grades)` — projected values override existing grades
- `calculate_target_gpa` — aggregate target-GPA estimate without reading OBS
- `check_course_conflicts` — scans one or more department codes
- `get_personal_exam_calendar` — authenticated student's official final calendar

### Public OBS and campus tools (no credentials)

- `obs_search_courses` / `obs_get_course_prerequisites`
- `get_public_course_schedule` / `get_public_course_prerequisites`
- `get_public_exam_schedule(department_code)` — official current final timetable
- `get_academic_calendar(date_from, date_to, category, query)`
- `list_degree_faculties` → `list_degree_programs` → `build_degree_plan`
- `find_open_course_sections` — capacity minus enrolled, across up to 25 selected departments
- `find_empty_classrooms` — coverage-limited estimate from selected schedules; does not include reservations
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
- `search_campus_locations` — official OBS building codes/names (not coordinates)
- `get_shuttle_schedule` / `get_sports_facility_hours`
- `get_itu_announcements` — İTÜ, ÖDEK, İKM, SKS and Erasmus aggregation

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

Every result carries a `coverage` field. An empty result means one of three different things
— the term was never captured (`term_missing`), the branch is absent from that term's dump
(`branch_absent_from_term`), or the filters matched nothing (`covered`) — and the tool always
says which. Quota data refreshes daily and is not live; check OBS before registering.

`explain_course_eligibility` also carries `archive_seasonality` when the archive has the
course: a course can be prerequisite-eligible right now and still only ever open in one term
a year, and this surfaces that without a separate lookup.

The archive client uses its own cookie-free session and a single-host HTTPS allowlist derived
from `ITU_ARCHIVE_BASE_URL`.

### Library tools

- `library_search` / `library_get_item` / `library_check_availability` are public.
- `library_get_account` / `library_list_loans` use the separate library variables above.
- `library_renew_loan` and `library_reserve_item` are dry-run by default and submit only
  with `confirm=true`. They are not exposed by the remote HTTP transport.
- If the library host presents an expired or otherwise invalid certificate, the client
  fails closed with a TLS error instead of disabling verification.

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
