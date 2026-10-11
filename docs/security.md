# Security Notes

This project logs in to İTÜ Ninova, OBS, Portal and optionally İTÜ Mail with credentials supplied by the user. Public İTÜ and library-catalog tools do not require those credentials.

## Do

- Use only your own İTÜ account.
- Keep `.env` private.
- Prefer local stdio MCP for Claude Desktop, Claude Code, Cursor, Codex, and OpenClaw.
- If you expose the remote HTTP transport, use HTTPS, a long random MCP path,
  `NINOVA_REMOTE_API_KEY`, and `NINOVA_REMOTE_REQUIRE_API_KEY=1`.
- The remote transport binds to `127.0.0.1` by default. Set
  `NINOVA_REMOTE_HOST=0.0.0.0` only in an isolated container or behind a trusted
  HTTPS reverse proxy/firewall.
- Rotate your Ninova password if you accidentally commit or share credentials.
- Rotate the remote API key if it leaks.
- Keep library account values in the separate `NINOVA_LIBRARY_NAME`,
  `NINOVA_LIBRARY_ID`, and `NINOVA_LIBRARY_PIN` variables.
- Treat announcement, assignment, catalog and other fetched text as untrusted data.
- Keep remote mail tools disabled unless they are explicitly needed.

## Do not

- Commit `.env`, cookies, downloaded course files, submissions, screenshots, or state folders.
- Deploy a public remote server with a predictable MCP path and no API key.
- Share your remote MCP URL or API key publicly.
- Use this to access accounts, courses, or files you are not authorized to access.
- Disable TLS verification to work around an expired library certificate.
- Follow instructions embedded in external tool-result text.

## Network boundaries

- Authenticated `NinovaClient` requests accept only HTTPS İTÜ domains and normal
  relative paths. Every redirect target is checked before the next request is sent;
  cross-host redirects also drop an `Authorization` header.
- `ObsPublicClient` talks only to exact allowlisted OBS and academic-calendar hosts.
- `ItuPublicClient` uses an exact host allowlist for OBS, Rehber, SKS, ÖDEK, İKM,
  Erasmus and the main İTÜ site. It owns a fresh `requests.Session`; SSO cookies are
  never copied into the public client.
- `LibraryClient` defaults to `https://katalog.kutuphane.itu.edu.tr` and retains
  explicit legacy configuration for `https://divit.library.itu.edu.tr`. Each instance
  permits only its configured exact host and normal HTTPS port, with no URL credentials.
  It validates TLS and fails closed. A custom CA can be supplied with
  `NINOVA_LIBRARY_CA_BUNDLE`; there is no insecure `verify=false` switch.
- `ItuMailClient` uses verified IMAPS, selects only `INBOX` in read-only mode, and
  fetches bodies and attachments with `BODY.PEEK` so unread state is preserved.
  It connects only to `imap.itu.edu.tr` on port 993. Any other `NINOVA_MAIL_HOST` or
  `NINOVA_MAIL_PORT` is refused before a connection is opened, wherever the value
  came from. The mail login sends the İTÜ password, and a valid TLS certificate
  only proves that a server owns its own name, so the name has to be the known one.
  The match is exact; other hosts under `itu.edu.tr` are not accepted.
- Public and library clients apply the same pre-request redirect validation, so an
  allowlisted page cannot bounce the client to localhost or an unrelated host.
- User input is sent as query/form values, never concatenated into an arbitrary host,
  command, or filesystem path by these public tools.

### Operator-configurable third-party sources

Two public data sources are not İTÜ systems, and the operator can point them
somewhere else:

- `ITU_ARCHIVE_BASE_URL` (default `https://itu-ders.com/data`), the course archive
  behind the `archive_*` tools and resources.
- `PREREQ_CROSSCHECK_BASE_URL`, the community prerequisite feed behind the
  `cross_check` field of `explain_course_eligibility`.

No İTÜ credential or session cookie is sent to them. What they return is
third-party text that reaches the model: course names, descriptions, instructor
names and prerequisite strings. It is marked as untrusted like every other result,
but whoever controls the configured host controls that text. Both variables are
read from the process environment only and are never loaded from a `.env` file, so
a file in the working directory cannot redirect them. Leave them unset unless you
run your own copy of the data.

## Configuration files

An MCP client decides the working directory the server starts in, and that
directory can be inside a tree somebody else wrote, such as a cloned repository.
A `.env` found there is therefore treated as less trusted than the process
environment:

- Only the working directory itself is checked for a `.env`. Parent directories
  are not searched.
- Variables already in the process environment always take precedence.
- A `.env` found this way cannot set the keys that decide where credentials go,
  where state is written or which protections are on: base URL and host overrides,
  the mail host and port, the library CA bundle and proxy, `NINOVA_STATE_DIR`,
  `NINOVA_OBS_REGISTRATION_WRITES` and the remote transport settings that would
  loosen it. `NINOVA_ALLOW_UPLOADS` and `NINOVA_REQUIRE_ELICITATION` can only be
  moved to the stricter value. A `NINOVA_*` key the server does not know is ignored
  as well.
- Each ignored key is named on stderr at startup. Values are never printed.
- A file named with `NINOVA_ENV_FILE` in the process environment is your explicit
  choice, so every `NINOVA_*` key in it counts.

What remains: a `.env` in the working directory can still supply
`NINOVA_USERNAME` and `NINOVA_PASSWORD` when the process environment has none. The
server would then log in with those values, to the real İTÜ hosts. Start the server
from a directory you trust, or pass credentials through the `env` block of the
client.

## Actions that change state

- `submit_assignment` is a dry-run unless `confirm=true`; it can be disabled with
  `NINOVA_ALLOW_UPLOADS=0` and is local-stdio only.
- `library_renew_loan` and `library_reserve_item` are dry-run unless `confirm=true`.
  Both resolve opaque identifiers from the current account/catalog page before a
  POST and are excluded from remote HTTP transport. These legacy operations are
  unavailable on the current Sirsi platform and fail before any account request.
- Course registration and course dropping are intentionally not implemented.
- `obs_validate_registration_plan` and `obs_get_elective_group` send CRNs to the OBS
  draft-check endpoint. They save nothing, but OBS holds a short transaction lock and
  counts each check against a quota, so both are annotated as non-read-only.
- `obs_save_registration_draft` replaces the saved registration draft. It is off unless
  `NINOVA_OBS_REGISTRATION_WRITES=1`, needs `confirm=true`, is never exposed on the
  remote HTTP transport, and refuses to drop courses from an existing draft unless
  `allow_replace=true`. The refusal lists what would be removed and sends nothing.
  It has not yet been confirmed against a successful live save.

### Confirmation by the user, not by the model

`confirm=true` and `allow_replace=true` are arguments, and arguments are written by the
model. A client that approves tool calls automatically, combined with an announcement or
an email that says "upload this file" or "save these CRNs", could otherwise produce a
write that no person ever looked at. `submit_assignment` and `obs_save_registration_draft`
therefore ask the user directly with MCP elicitation: the server sends the question, the
client shows it, and the model cannot answer it.

- The question is the last gate. The environment switches, input validation, the
  dry-run preview and the `needs_allow_replace` refusal all run first, and none of them
  is relaxed. `confirm` and `allow_replace` stay required exactly as before. Supplying
  them never skips the question.
- The question is written in Turkish and states the concrete action. For a draft: the
  term code, the CRNs that will be saved, the CRNs that will be added and the CRNs that
  will be removed from the current draft. For an upload: course, assignment, upload
  address, slot, file name, size and local path, and whether an existing submission
  will be replaced. Course and assignment names come from Ninova pages, so each one is
  reduced to a single truncated line and cannot add lines of its own to the dialog.
- Only an explicit accept with the confirmation box ticked proceeds. Decline, cancel, an
  unticked box, a malformed answer, a client error and a request cancelled while the
  dialog is open all send nothing. Decline and cancel come back as a normal result
  (`status: declined_by_user` or `cancelled_by_user`) that tells the model not to retry,
  not as an error.
- A client that did not declare the elicitation capability keeps the previous behaviour:
  the arguments alone gate the write. The result then carries
  `confirmation.method = "argument_only"` so the transcript shows that no person was
  asked by the server.
- `NINOVA_REQUIRE_ELICITATION=1` turns that fallback into a refusal
  (`status: elicitation_required`, nothing sent). It is off by default so existing
  setups keep working. Set it when the account should never be written to without a
  dialog. The value must be exactly `1`. With it set, a direct Python call of the two
  methods outside an MCP request is refused as well.
- Limits: the server can only see what the client declares. A client that declares
  elicitation and then answers the question itself, without showing it, defeats this
  check; the protocol gives the server no way to detect that. Whether a given client
  shows elicitation dialogs has to be checked for that client and version.

## Prompt-injection boundary

İTÜ pages can contain text written by instructors, students or external editors.
HTML sanitization cannot reliably distinguish a legitimate sentence from a malicious
instruction aimed at an LLM. Every MCP dictionary result is therefore marked with
`untrusted_external_content` and a `content_notice`; the server instructions tell the
client to treat embedded instructions as data. This provenance marker is preserved on
raw Ninova/OBS content as well as public announcements and library records.
Mail bodies and attachment content cross the same untrusted-content boundary.

## Data stored locally

Depending on the tools you call, the server may create:

- `~/.ninova_state/` for snapshots, tracking state, the seat-watch list, downloads,
  and optionally `session.json` (session cookies only, never the password).
  Override with `NINOVA_STATE_DIR`.
- any explicit output directory you request through tools

These paths are ignored by git by default.

File permissions:

- Linux and macOS: `session.json`, the tracking state and the seat-watch state are
  created with mode `0600`, and their directory with `0700`. An existing state
  directory with wider permissions is tightened when one of these files is written.
  The temporary file used during a write has mode `0600` from the start, and it
  replaces the old file in one step, so an interrupted write leaves the old file
  intact. A transcript saved by `obs_download_transcript` is `0600` as well; the
  directory you chose for it is left as it is.
- Windows: there are no such permission bits, and `chmod` there only toggles the
  read-only flag. The files are protected by the access control list of your user
  profile, where `~/.ninova_state` lives: other standard accounts on the machine
  cannot read it, administrators can. If you move the state directory with
  `NINOVA_STATE_DIR`, it gets the access rules of the place you chose. The server
  does not change Windows ACLs.
- Page snapshots and files saved by `download_resource` are written with the
  default permissions of the process on every platform. Inside the default state
  directory they are covered by its `0700` mode on Linux and macOS, once a
  session, tracking or seat-watch file has been written there.
