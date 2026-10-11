# Tracking completeness

`sync_all_courses` records a separate observation for each course section and
for enrollment discovery. A successful HTTP response alone does not establish
that an inventory is complete.

Each `coverage` entry has a `status`:

- `complete`: the requested inventory was recognized and fully traversed.
- `partial`: some data was read, but a depth limit, item limit, pagination,
  malformed row, or subsidiary request prevented a complete inventory.
- `failed`: the request or parser failed, or the response was a login or
  unexpected page.
- `skipped`: the caller did not request this section.
- `unknown`: the response structure does not establish completeness.

`snapshot_complete` is true only when every section is complete. Skipping files
therefore leaves it false even if all requested non-file sections succeed.
Skipped sections are not reported as errors. Other incomplete sections include
their reason in the sync response's `errors`, with course and section context.

Incomplete sections retain their previous data. Their metadata records the
latest attempt's status, `data_retained`, and, when known,
`last_complete_at`. Retaining data does not make it current. The metadata also
retains `baseline_complete`, so a later successful read compares against the
last complete observation rather than generating artificial added events.
When assignment details are skipped, previously read detail fields are retained
with `details_retained` and `details_last_observed_at`.

Only a complete current section with a complete previous baseline produces
added, changed, or removed events. File traversal exposes unread folders and
failed pages. Reducing the maximum depth cannot make deeper files appear
deleted. Recognized pagination and the 200-item assignment/announcement cap
produce a partial observation instead of a truncated complete inventory.

## Enrollment limits

The internal course list is read before compact output is applied. Compact mode
cannot truncate the list used by synchronization. A partial or unrecognized
dashboard does not replace a usable course cache.

Generic `/Sinif/` links on the dashboard do not prove that the complete
enrollment list was read. They currently produce `enrollment_coverage.status =
"unknown"`. Per-course reads still run, but absent courses are retained and no
course added/removed events are inferred from that discovery. A future parser
can certify a verified enrollment container. Explicit statements that there are
no enrolled classes can establish a complete empty inventory. A login page,
unrecognized empty page, or absence statement for another section cannot.

## Existing state

State version 1 has no completeness metadata. It remains readable. Its section
baselines are treated as unknown. The first complete observation of each
section establishes a new baseline without claiming historical changes. The
next complete observation can produce changes normally. A missing course from
legacy state is retained during the first complete enrollment observation and
can be removed on a subsequent complete observation.

Synchronization writes version 2 metadata. It does not reinterpret or delete
existing change history. Previously recorded suspicious events require a
separate review.

## Course references

Ninova resolution uses the shared course-code parser. Compact, spaced,
case-insensitive, and non-breaking-space spellings resolve consistently.
English/lab suffixes remain distinct. A complete course code must match a
complete code, while ordinary course titles still support text lookup.

## Seat watch

During registration and add/drop weeks a full section can open at any moment.
The seat watch tools keep a short list of CRNs and report what changed in the
public OBS course schedule since the last look. They need no login.

- `watch_course_sections(crns, department_code?, program_type?)` adds sections.
- `unwatch_course_sections(crns? | stale_only? | clear_all?)` removes them.
- `list_watched_course_sections` shows the stored list without reading OBS.
- `check_watched_course_sections` reads the schedule and reports the changes.

### It does not run in the background

An MCP server only does something when a client calls one of its tools. This
server has no timer, no background thread and no way to send a notification.
Adding a CRN to the watch list does not start any monitoring. Seats are read
only at the moment `check_watched_course_sections` is called.

To be told when a seat opens, something on the client side has to call the
check periodically: a scheduled task in the MCP client (for example a recurring
prompt such as "check my watched sections and tell me only if one opened"), or
the user asking again. Between two checks nothing is observed, so a seat that
opens and fills again in between is not seen.

### Finding a section

A CRN alone does not locate a section in OBS. The public schedule is read per
program level and branch, so the tool needs both.

- With `department_code` (and `program_type`, default `LS`) the CRN is looked up
  in that branch's schedule.
- Without it, the branch and level come from the course archive's whole-term
  search index (the data behind `archive_search_sections`), which maps every
  CRN of the current term to its branch. The match is then confirmed against
  the live OBS schedule. This is only used while the archive's current term is
  the term OBS is serving. If they differ, the tool asks for `department_code`
  instead of guessing.

A CRN that is absent from the schedule is returned under `not_found` and is not
added.

### Reading a check

Each section has a `status`:

- `open`: capacity is above enrolled. `seats_free` is the difference.
- `full`: enrolled has reached capacity. `over_capacity` is set when it exceeds it.
- `no_capacity`: OBS lists the section with a capacity of 0.
- `unknown`: OBS gave no usable numbers. See below.
- `not_listed`: the CRN is absent from a schedule that was read successfully.
  The section may have been cancelled or moved. This is not the same as full.
- `stale_term`: the entry belongs to another term. See below.
- `fetch_failed`, `schedule_unavailable`, `term_unverified`: the branch could
  not be read, came back empty or unparsed, or OBS did not report the active
  term. The previous observation is kept and nothing is compared.

`changes` names what moved since the previous check: `opened` (full or no
capacity to open), `filled` (open to full), `capacity_raised`,
`capacity_lowered`, `seats_changed`, `enrollment_changed`, `delisted`,
`relisted`, `data_unavailable`, `data_available`. `compared_with` holds the
earlier figures. `summary` lists the CRNs per change so a client can answer
"did anything open" without reading every section. `last_change` keeps the most
recent change of a section visible on later checks.

The first check of a section returns `changes: ["baseline"]`. It records the
numbers and claims no change. The `current` figures returned by
`watch_course_sections` are informational and are not a baseline.

`seats_free` is capacity minus enrolled. Part of a section's capacity can be
reserved for particular programs. When OBS lists a reservation the section
carries the raw `reservation` text, and a free seat may not be available to
every student.

### Unknown is not zero

A missing or non-numeric capacity or enrolled value makes the section
`unknown`, with `unknown_reason` set to `capacity_missing` or
`enrolled_missing`.

At some points in the term OBS shows 0 enrolled for every section because the
column has not been filled yet. When no section of a branch has anyone
enrolled, the branch is treated as not populated: its watched sections are
`unknown` with `unknown_reason: "enrollment_not_populated"`, and the source
entry has `enrollment_populated: false`. A very small branch whose sections are
all truly empty is reported the same way. That is the intended trade: the tool
would rather say it does not know than announce a seat from a placeholder.

An `opened` change is only reported between two observations that both carry
real numbers. A check that could not read the numbers does not erase the last
real observation, so full, then unknown, then full reports no opening, and
full, then unknown, then open reports `opened`.

### Requests and caching

A check sends one schedule request per watched branch, not one per CRN, through
the same client as `get_public_course_schedule`, so `NINOVA_REQUEST_DELAY_MS`
applies. Entries from a past term cost no schedule request.

At most 20 sections can be watched. A student registers for six to eight
courses and may watch a few alternative sections for each. The limit keeps the
worst case of a check at 20 OBS requests, below the 25 branch ceiling that
`find_open_course_sections` applies to one call.

Schedule reads are cached for `NINOVA_PUBLIC_SCHEDULE_CACHE_TTL_SECONDS`
(default 60). A check inside that window is answered from the cache. The result
says so: each entry in `sources` has `fetched_at`, `from_cache` and
`age_seconds`, and `freshness` has `all_from_cache`, `oldest_data_age_seconds`,
`cache_ttl_seconds` and, when everything came from the cache,
`retry_after_seconds`. A section whose numbers come from the same OBS read as
the previous check has `repeat_of_last_check: true` and no new comparison is
made. Calling the check in a tight loop therefore returns the same numbers and
reaches OBS at most once per TTL. A sensible interval for a scheduled check is
a few minutes.

### Terms

OBS reuses CRNs across terms. Every watch entry stores the term it was added
in. On a check, an entry whose term is not the active OBS term is returned as
`stale_term` with its old observation. It is not compared with the current
schedule, because the same CRN may now belong to a different course.
`unwatch_course_sections(stale_only=true)` removes these entries. Adding the
same CRN again in the new term replaces the old entry and starts a new
baseline.

### State

The watch list and the last observations are stored in
`seat-watch-state.json` under `NINOVA_STATE_DIR`, next to the Ninova tracking
state and separate from it. The file has a `version` field (currently 1) and is
written to a temporary file and renamed, so an interrupted write leaves the
previous file intact. A missing file is an empty list. A file that cannot be
parsed is also treated as an empty list and the tools return a `state_warning`.
The server does not crash, and the file is rewritten on the next change.

The three tools that write this file (`watch_course_sections`,
`unwatch_course_sections`, `check_watched_course_sections`) are announced to
clients as not read-only, like `sync_all_courses`. `list_watched_course_sections`
is read-only. All four are available on the remote HTTP transport, again like
`sync_all_courses`: they write only inside the server's own state directory and
never touch a university account. A remote server is single-tenant, so everyone
who can reach it shares one watch list.
