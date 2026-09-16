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
