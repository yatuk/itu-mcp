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
export NINOVA_REQUIRE_ELICITATION="0"                # 1 = refuse account writes unless the client can ask the user
export NINOVA_OBS_BASE_URL="https://obs.itu.edu.tr"  # OBS student portal
export NINOVA_OBS_JWT_TTL_SECONDS="1500"
export NINOVA_OBS_PUBLIC_CACHE_TTL_SECONDS="3600"    # public catalog metadata
export NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS="60" # capacity/schedule reads
export NINOVA_ITU_PUBLIC_CACHE_TTL_SECONDS="300"     # directory/SKS/news pages
export NINOVA_LIBRARY_CACHE_TTL_SECONDS="300"
export NINOVA_MAIL_HOST="imap.itu.edu.tr"
export NINOVA_MAIL_PORT="993"
export NINOVA_MAIL_TIMEOUT_SECONDS="15"
export ITU_ARCHIVE_BASE_URL="https://itu-ders.com/data"  # course archive
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

### Toolsets (choosing which tool groups are published)

The server has more than 100 tools, and an MCP client loads every tool's name, description and
schema at the start of each conversation. If you only use part of the server, publish
only the groups you need. Each tool belongs to exactly one group:

| Toolset | Tools | What it covers |
|---|---|---|
| `core` | 3 | Always on: `auth_status`, `refresh_session`, `obs_auth_status` |
| `ninova` | 17 | Ninova courses: dashboard, announcements, files, assignments, grades, upload |
| `tracking` | 3 | `sync_all_courses`, `get_updates`, `get_upcoming_deadlines` |
| `files` | 6 | Read, crawl and download Ninova pages and files, text extraction, snapshots |
| `obs` | 14 | OBS student record: profile, grades, schedule, attendance, transcript |
| `registration` | 5 | OBS registration status, draft, elective groups, plan check |
| `portal` | 5 | Campus card, notifications, help tickets, cloud and mail quota |
| `planning` | 9 | GPA, relative grade, conflicts, degree plan, eligibility |
| `public` | 8 | Public OBS catalog: course search, schedules, prerequisites, exams, calendar |
| `campus` | 6 | Cafeteria, shuttle, directory, buildings, sports hours, announcements |
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
export NINOVA_REMOTE_ENABLE_MAIL="0"                   # explicit opt-in for mail tools
export NINOVA_REMOTE_ALLOWED_TOOLS=""                  # optional comma-separated allowlist
export NINOVA_REMOTE_TRUST_PROXY_HEADERS="1"             # only if genuinely behind a proxy
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
`NINOVA_REMOTE_DISABLE_RATE_LIMIT=1` if needed. The connecting socket's IP is used unless
`NINOVA_REMOTE_TRUST_PROXY_HEADERS=1` is set — only turn that on if a real reverse proxy sits
in front of the server, since `X-Forwarded-For` is otherwise a client-supplied header anyone
can rotate to dodge the limit.

The toolset variables above (`NINOVA_TOOLSETS`, `NINOVA_DISABLED_TOOLSETS`) also apply to
the remote server, and they can only narrow its tool list. Tools that are never served
remotely stay excluded, mail tools still need `NINOVA_REMOTE_ENABLE_MAIL=1`, and
`NINOVA_REMOTE_ALLOWED_TOOLS` is applied last: naming a tool from a switched-off toolset
there is a startup error, and if you set it, list `auth_status` yourself to keep it.

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
- `NINOVA_REQUIRE_ELICITATION=1`: clients without elicitation are refused with
  `status: elicitation_required`. Default is off.

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
- `obs_download_transcript` — save transcript PDF under state dir; writes to a caller-supplied
  directory, so like `download_resource`/`snapshot_page` it is not exposed by the remote HTTP
  transport

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
- `obs_calculate_gpa(projected_grades)` — projected values override existing grades
- `calculate_target_gpa` — aggregate target-GPA estimate without reading OBS
- `estimate_relative_grade(class_scores, my_score)` — likely letter grade under İTÜ's
  bağıl değerlendirme rules, by both official methods (T-score against an example
  class-level table, and mean ± standard-deviation multiples); pure local computation,
  no OBS call. The example table is exactly that — an example — so treat the result as
  a planning estimate, not the official grade.
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
- `get_academic_calendar(date_from, date_to, category, query)`
- `list_degree_faculties` → `list_degree_programs` → `build_degree_plan`
- `find_open_course_sections` — capacity minus enrolled, across up to 25 selected departments
- `find_empty_classrooms` — coverage-limited estimate from selected schedules; does not include reservations
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
- `search_campus_locations` — official OBS building codes/names (not coordinates)
- `get_shuttle_schedule` / `get_sports_facility_hours`
- `get_itu_announcements` — İTÜ, ÖDEK, İKM, SKS and Erasmus aggregation
- `export_course_schedule_ics(crns, department_codes, program_type?, term_start?, term_end?)`:
  the weekly meetings of the given CRNs as iCalendar (RFC 5545) text, read from the public
  course schedule. Each meeting is a weekly recurring event between the first and last day
  of classes. Pass those as `term_start` and `term_end` (`YYYY-MM-DD`). When one is omitted
  the tool looks for "Beginning of ... Term" / "End of ... Term" on the academic calendar
  page for the same academic year and level, but that page covers only the displayed month,
  so most calls without dates return `status: "term_dates_required"`, `ics: null` and the
  names of the missing parameters. No date is ever guessed. A meeting whose day or time is
  an OBS placeholder (`-`, `--`, `-/-`) is listed in `skipped` with a reason and gets no
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
says which. Quota data refreshes daily and is not live; check OBS before registering.

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

- `itu://calendar/academic`: the academic calendar page as `get_academic_calendar` returns
  it. It covers the displayed month only; check `coverage`
- `itu://archive/terms`: the terms the course archive holds, the current term slug and the
  known gaps
- `itu://campus/buildings`: official OBS building codes and names

Resource templates:

- `itu://archive/catalog/{course_code}`: the archive catalog entry of a course, the same
  payload as `archive_course_catalog`
- `itu://archive/grades/{course_code}`: the historical letter grade distribution of a
  course, the same payload as `archive_grade_distribution` over every recorded term
- `itu://degree-plan/{program_code}`: the latest official undergraduate degree plan of an
  OBS program code such as `BLGE_LS`, with older versions under `available_plans`. The
  public form is keyed by faculty, so the faculty is found by reading each faculty's
  program list (cached for an hour); the first read of a session is therefore slower

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
