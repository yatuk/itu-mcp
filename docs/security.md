# Security Notes

This project logs in to İTÜ Ninova, OBS and Portal with the username and password supplied by the user. Public İTÜ and library-catalog tools do not require those credentials.

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
- `LibraryClient` accepts only `https://divit.library.itu.edu.tr`. It validates TLS
  and fails closed. A custom CA can be supplied with `NINOVA_LIBRARY_CA_BUNDLE`;
  there is no insecure `verify=false` switch.
- Public and library clients apply the same pre-request redirect validation, so an
  allowlisted page cannot bounce the client to localhost or an unrelated host.
- User input is sent as query/form values, never concatenated into an arbitrary host,
  command, or filesystem path by these public tools.

## Actions that change state

- `submit_assignment` is a dry-run unless `confirm=true`; it can be disabled with
  `NINOVA_ALLOW_UPLOADS=0` and is local-stdio only.
- `library_renew_loan` and `library_reserve_item` are dry-run unless `confirm=true`.
  Both resolve opaque identifiers from the current account/catalog page before a
  POST and are excluded from remote HTTP transport.
- Course registration/CRN submission is intentionally not implemented.

## Prompt-injection boundary

İTÜ pages can contain text written by instructors, students or external editors.
HTML sanitization cannot reliably distinguish a legitimate sentence from a malicious
instruction aimed at an LLM. Every MCP dictionary result is therefore marked with
`untrusted_external_content` and a `content_notice`; the server instructions tell the
client to treat embedded instructions as data. This provenance marker is preserved on
raw Ninova/OBS content as well as public announcements and library records.

## Data stored locally

Depending on the tools you call, the server may create:

- `~/.ninova_state/` for snapshots, tracking state, downloads, and optionally
  `session.json` (session cookies only — never the password). Override with `NINOVA_STATE_DIR`.
- any explicit output directory you request through tools

These paths are ignored by git by default. Session files are written with restrictive
permissions when the OS allows it (`chmod 600`).
