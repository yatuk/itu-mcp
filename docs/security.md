# Security Notes

This project logs in to İTÜ Ninova, OBS, Portal and optionally İTÜ Mail with credentials supplied by the user. Public İTÜ and library-catalog tools do not require those credentials.

## Do

- Use only your own İTÜ account.
- Keep `.env` private.
- Prefer local stdio MCP for Claude Desktop, Claude Code, Cursor, Codex, and OpenClaw.
- If you expose the remote HTTP transport, use HTTPS and a long random
  `NINOVA_REMOTE_API_KEY`, and set `NINOVA_ALLOWED_HOSTS` or `NINOVA_PUBLIC_BASE_URL`.
  A long random MCP path is a useful extra, not a replacement for the key.
- The remote transport binds to `127.0.0.1` by default. Set
  `NINOVA_REMOTE_HOST=0.0.0.0` only in an isolated container or behind a trusted
  HTTPS reverse proxy/firewall. On such a bind the server refuses to start without a
  key and without the expected Host values.
- Rotate your Ninova password if you accidentally commit or share credentials.
- Rotate the remote API key if it leaks.
- Keep library account values in the separate `NINOVA_LIBRARY_NAME`,
  `NINOVA_LIBRARY_ID`, and `NINOVA_LIBRARY_PIN` variables.
- Treat announcement, assignment, catalog and other fetched text as untrusted data.
- Keep remote mail tools disabled unless they are explicitly needed.

## Do not

- Commit `.env`, cookies, downloaded course files, submissions, screenshots, or state folders.
- Set `NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1` or
  `NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1` on a server that others can reach.
- Share your remote MCP URL or API key publicly.
- Use this to access accounts, courses, or files you are not authorized to access.
- Disable TLS verification to work around an expired library certificate.
- Follow instructions embedded in external tool-result text.

## Network boundaries

- Every host check runs on the URL that the HTTP library will really send, read with
  the parser that library uses. `urllib.parse` and `requests`/urllib3 disagree about
  some URLs (`https://other.example\@ninova.itu.edu.tr/` is one host to the first and
  another to the second), so a check on the caller's text is not enough. A URL that
  contains a backslash, whitespace, an ASCII control character or user information
  (`user@host`) is refused before anything is parsed or sent. The same applies to every
  redirect `Location`; the only tolerance there is a literal space in the path or query,
  which is percent-encoded before the check. Hosts are compared exactly, in lower case;
  a trailing-dot host, another scheme and (for the authenticated client) a port other
  than 443 are refused.
- A URL or path that comes from a tool argument can reach one host only: the Ninova
  host (`ninova.itu.edu.tr`). This covers `read_page`, `download_resource`,
  `read_resource_text(url=)`, `snapshot_page`, `diff_snapshot`, `crawl_course`,
  `get_assignment_upload_slots` and `submit_assignment`. Such a request may be
  redirected to the sign-on host and back, nowhere else, and a response that ends on
  another host is discarded. Tool arguments never select the host.
- The other hosts the authenticated session needs are an exact list, with no suffix
  match: `girisv3.itu.edu.tr` (single sign-on), `portal.itu.edu.tr`, `obs.itu.edu.tr`
  and `yardim.itu.edu.tr`. They are reachable only from the fixed code paths of the
  portal, OBS and help desk tools, which build their own URLs. If İTÜ adds a hop to the
  sign-on redirect chain, the operator can list it in `NINOVA_EXTRA_SSO_HOSTS` (exact
  host names under `itu.edu.tr`, comma separated, default empty). Such a host can only
  be passed through; a tool argument still cannot name it.
- The password is sent only to the sign-on page on `girisv3.itu.edu.tr`. A page on any
  other host that merely contains the login form gets nothing, the browser fallback
  checks the page address before typing, and a redirect cannot replay the login form
  or an uploaded file to another host.
- Every redirect target is checked before the next request is sent; cross-host
  redirects also drop an `Authorization` header.
- `ObsPublicClient` talks only to exact allowlisted OBS and academic-calendar hosts.
- `ItuPublicClient` uses an exact host allowlist for OBS, Rehber, SKS, ÖDEK, İKM,
  Erasmus and the main İTÜ site. It owns a fresh `requests.Session`; SSO cookies are
  never copied into the public client.
- `LibraryClient` defaults to `https://katalog.kutuphane.itu.edu.tr` and retains
  explicit legacy configuration for `https://divit.library.itu.edu.tr`. Each instance
  permits only its configured exact host and normal HTTPS port, with no URL credentials.
  It validates TLS and fails closed. A custom CA can be supplied with
  `NINOVA_LIBRARY_CA_BUNDLE`; there is no insecure `verify=false` switch.
- `ItuMailClient` uses verified IMAPS, selects `INBOX` or the existing sent folder in read-only mode, and
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

## Where the password lives

There are three ways to give the server your İTÜ password. It looks for it in
this order and uses the first one it finds:

| Setup | Where the password is stored | Who can read it |
| --- | --- | --- |
| Client config (`env` block of the MCP client) | Plain text in the client's JSON config file | Anyone and anything that can read that file, including backups and synced copies of it |
| `.env` file (working directory, or `NINOVA_ENV_FILE`) | Plain text in that file | Anyone and anything that can read that file |
| OS keychain (`itu-mcp setup`) | Windows Credential Manager, macOS Keychain, or Secret Service / KWallet on Linux, under the service name `itu-mcp` | Any process running as your user account |

Notes on the keychain:

- It keeps the password out of config files, shell history, backups of your
  dotfiles and screenshots of your settings. It does **not** protect it from
  software running as you: any program started under your account can ask the
  keychain for the entry, the same way this server does. macOS may ask you to
  approve the first read; Windows and most Linux desktops do not.
- Only real operating system stores are used. File based and plain text
  `keyring` backends (`keyrings.alt`), the `fail` backend and the `null` backend
  are refused, and `itu-mcp setup` then prints the manual path instead of
  storing anything.
- `keyring` is an optional dependency (`pip install "itu-mcp[keyring]"`). Without
  it the server behaves as before and reads only the environment and `.env`.
- `NINOVA_KEYRING=0` switches the keychain lookup off.
- `itu-mcp connect` writes a client config entry with a command and arguments
  only. If an existing `itu` entry holds `NINOVA_PASSWORD`, it asks before
  replacing it and leaves it alone with `--yes`. The backup written next to the
  config still contains the old password: delete it once the new setup works.
- `itu-mcp doctor` reports where the credentials come from and whether a client
  config holds the password in plain text. It never prints the values.
- `itu-mcp logout --forget-password` removes the keychain entries.

In every setup the password is held in the memory of the running server and is
sent only to the pinned İTÜ hosts listed under "Network boundaries".

## Remote HTTP transport

Whoever can call the remote endpoint acts as the student whose credentials the server
holds. The transport is therefore closed by default
([startup table and variables](advanced.md#remote-http-server-chatgpt--claudeai-custom-connectors)):

- A non-loopback bind does not start without `NINOVA_REMOTE_API_KEY` and without
  `NINOVA_ALLOWED_HOSTS` or `NINOVA_PUBLIC_BASE_URL`. The Docker image binds `0.0.0.0`,
  so the same holds for it. On loopback with no key, a random one-time key is generated
  and printed once to stderr.
- The two opt-outs, `NINOVA_REMOTE_ALLOW_UNAUTHENTICATED=1` and
  `NINOVA_DISABLE_DNS_REBINDING_PROTECTION=1`, must be set explicitly and print a
  warning on every start.
- Host and Origin validation is always on for the MCP path, on every bind.
- The middleware is default deny. Only `GET`/`HEAD` on exactly `/` and `/healthz` are
  served without the key. Every other path, known or not, needs the key and is rate
  limited. The MCP path cannot be `/`, empty or a status route.
- Without the key a caller can learn only this: that an `itu-mcp` server is running
  (`GET /`), that it is alive (`GET /healthz`), with `NINOVA_HEALTHZ_CHECK_AUTH=1`
  whether its stored login currently works (cached, at most one check per interval),
  and from `401`, `421` and `429` answers that a key, a Host check and a rate limit
  exist. The MCP path, version, state directory and settings are not returned.
- The key is compared in constant time. Failed attempts count against the rate limit.
  There is no lockout and no per-user identity: everyone with the key is the same
  caller. Use HTTPS in front, since the key travels in a header.
- `X-Forwarded-For` is ignored unless `NINOVA_REMOTE_TRUST_PROXY_HEADERS=1`, and is then
  read from the right, skipping `NINOVA_REMOTE_TRUSTED_PROXY_HOPS` trusted proxies.
- `submit_assignment` and `obs_save_registration_draft` are checked three times before
  the server starts (excluded set, selection filter, final assertion) and are never
  registered on this transport, whatever the environment says.
- The Docker image runs as a non-root user and keeps state in the `/data` volume.

## Actions that change state

- `submit_assignment` is a dry-run unless `confirm=true`; it can be disabled with
  `NINOVA_ALLOW_UPLOADS=0` and is local-stdio only. The upload page must be on the
  Ninova host with the path `/Sinif/<id>.<id>/Odev/<id>/OdevGonder`, and the page's
  form must post back to the upload page of the same assignment; anything else is
  refused before the user is asked. The file is read once, before the question. The
  question shows the real POST address, the size and the first 16 hex digits of the
  SHA-256 of those bytes, and exactly those bytes are sent even if the file changes on
  disk afterwards.
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
  address, the address the file is posted to, slot, file name, size, a SHA-256 prefix
  of the content and local path, and whether an existing submission will be replaced. Course and assignment names come from Ninova pages, so each one is
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
  dialog. `1`, `true`, `yes` and `on` enable it, `0`, `false`, `no` and `off` disable
  it, in any letter case. Any other non-empty value is an error and no write goes
  through, so a typo cannot leave the weaker fallback on unnoticed. With it set, a
  direct Python call of the two methods outside an MCP request is refused as well.
- Limits: the server can only see what the client declares. A client that declares
  elicitation and then answers the question itself, without showing it, defeats this
  check; the protocol gives the server no way to detect that. Whether a given client
  shows elicitation dialogs has to be checked for that client and version.

## Local files

Tool arguments are written by the model, and the model reads text that other people
wrote. The file tools therefore do not let an argument choose a place on disk.

- `read_resource_text(path=...)` reads only inside the downloads directory
  (`<NINOVA_STATE_DIR>/downloads`). A bare file name is looked up there. The path is
  resolved with symbolic links and junctions followed and must still be inside; `..`,
  absolute paths elsewhere, other drives, and UNC or device paths (`\\server\share`,
  `\\?\`) are refused, the last ones without touching the filesystem. Files larger
  than 25 MiB are refused, and a PDF is read up to 50 pages with the result saying that
  it was truncated.
- `download_resource` and `obs_download_transcript` write only inside the downloads
  directory. `output_dir` may name a subdirectory of it. An existing file is never
  replaced by `download_resource`: the new one gets `-2`, `-3` and so on.
- File names are made safe: path separators and control characters are replaced,
  trailing dots and spaces are stripped, reserved Windows device names (`CON`, `NUL`,
  `COM1` and the like) are refused, and a file whose type runs code when opened
  (`.exe`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.lnk`, `.url`, `.msi`, `.jar`,
  `.desktop` and similar) is saved with `.download` appended. This is a denylist, not
  an allowlist of document types, because course material is legitimately source code,
  archives and data in formats nobody can enumerate; the confinement above is what
  keeps a file out of a start-up folder.
- Downloads are limited to 200 MiB, checked while streaming. `NINOVA_MAX_DOWNLOAD_MB`
  changes the limit.
- `diff_snapshot(snapshot_path=...)` reads only inside the snapshot directory.
- The operator can allow more directories with `NINOVA_EXTRA_READ_DIRS` (reading) and
  `NINOVA_EXTRA_DOWNLOAD_DIRS` (writing): absolute directories separated by the
  platform's path separator (`;` on Windows, `:` elsewhere), default empty. A tool
  argument cannot add one. Even when such a directory contains the state directory,
  the state directory's own files (`session.json`, tracking state, snapshots) stay out
  of reach.

## Sensitive output

`obs_get_profile`, `obs_get_contacts` and `get_help_ticket` accept
`include_sensitive=true`, which is again an argument the model writes. It is honoured
only when the operator has set `NINOVA_ALLOW_SENSITIVE_OUTPUT=1` (default off).
Otherwise the redacted result is returned together with
`sensitive_output: "disabled_by_operator"` and a note that names the variable.

## Prompt-injection boundary

İTÜ pages can contain text written by instructors, students or external editors.
HTML sanitization cannot reliably distinguish a legitimate sentence from a malicious
instruction aimed at an LLM. Every MCP dictionary result is therefore marked with
`untrusted_external_content` and a `content_notice`. The marker is assigned by the
server after the tool returns, so a payload that carries its own value for that key
cannot switch it off. The server instructions tell the
client to treat embedded instructions as data. This provenance marker is preserved on
raw Ninova/OBS content as well as public announcements and library records.
Mail bodies and attachment content cross the same untrusted-content boundary.

## Data stored locally

Depending on the tools you call, the server may create:

- `~/.ninova_state/` for snapshots, tracking state, the seat-watch list, downloads,
  and optionally `session.json` (session cookies only, never the password).
  Override with `NINOVA_STATE_DIR`.
- files in a directory listed in `NINOVA_EXTRA_DOWNLOAD_DIRS`, if the operator set one

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
- Page snapshots and files saved by `download_resource` are written `0600` on
  Linux and macOS, and the state, snapshot and downloads directories are `0700`
  from startup. A directory listed in `NINOVA_EXTRA_DOWNLOAD_DIRS` keeps its own
  mode; the files written into it are still `0600`.
