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
- Public and library clients apply the same pre-request redirect validation, so an
  allowlisted page cannot bounce the client to localhost or an unrelated host.
- User input is sent as query/form values, never concatenated into an arbitrary host,
  command, or filesystem path by these public tools.

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
`untrusted_external_content` and a `content_notice`. The marker is assigned by the
server after the tool returns, so a payload that carries its own value for that key
cannot switch it off. The server instructions tell the
client to treat embedded instructions as data. This provenance marker is preserved on
raw Ninova/OBS content as well as public announcements and library records.
Mail bodies and attachment content cross the same untrusted-content boundary.

## Data stored locally

Depending on the tools you call, the server may create:

- `~/.ninova_state/` for snapshots, tracking state, downloads, and optionally
  `session.json` (session cookies only — never the password). Override with `NINOVA_STATE_DIR`.
- any explicit output directory you request through tools

These paths are ignored by git by default. Session files are written with restrictive
permissions when the OS allows it (`chmod 600`).
