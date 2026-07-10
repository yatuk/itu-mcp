# Security Notes

This project logs in to İTÜ Ninova and OBS with the username and password supplied by the user.

## Do

- Use only your own İTÜ account.
- Keep `.env` private.
- Prefer local stdio MCP for Claude Desktop, Claude Code, Cursor, Codex, and OpenClaw.
- If you expose the remote HTTP transport, use HTTPS, a long random MCP path,
  `NINOVA_REMOTE_API_KEY`, and `NINOVA_REMOTE_REQUIRE_API_KEY=1`.
- Rotate your Ninova password if you accidentally commit or share credentials.
- Rotate the remote API key if it leaks.

## Do not

- Commit `.env`, cookies, downloaded course files, submissions, screenshots, or state folders.
- Deploy a public remote server with a predictable MCP path and no API key.
- Share your remote MCP URL or API key publicly.
- Use this to access accounts, courses, or files you are not authorized to access.

## Data stored locally

Depending on the tools you call, the server may create:

- `~/.ninova_state/` for snapshots, tracking state, downloads, and optionally
  `session.json` (session cookies only — never the password). Override with `NINOVA_STATE_DIR`.
- any explicit output directory you request through tools

These paths are ignored by git by default. Session files are written with restrictive
permissions when the OS allows it (`chmod 600`).
