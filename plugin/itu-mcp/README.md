# İTÜ MCP

Ask Claude about your own Istanbul Technical University (İTÜ) courses and records: what is due this week, your grades and what your GPA would be with different results, your schedule and exams, which sections still have seats, what is left to graduate, and the history of any course. Unofficial and open source; not affiliated with İTÜ.

## What it contains

- A local MCP server (`itu-mcp`, started with `uvx itu-mcp`) with about 110 tools for Ninova, OBS, the student Portal, the public course schedule, the course archive, the library catalog and campus services.
- A skill that tells Claude which tool answers which kind of question.

The server is a local program, so the plugin works in Claude Code and in Cowork sessions that run on your computer. Chat on claude.ai does not start local servers.

## Set it up

1. Install [uv](https://docs.astral.sh/uv/) so that `uvx` is on your PATH.
2. Give the server your own İTÜ account through environment variables before starting Claude:

   ```bash
   export NINOVA_USERNAME="your-itu-username"
   export NINOVA_PASSWORD="your-itu-password"
   ```

   On Windows use `setx NINOVA_USERNAME "..."` and `setx NINOVA_PASSWORD "..."`, then open a new terminal. You can also keep them in a file and point `NINOVA_ENV_FILE` at it.
3. Add the plugin:

   ```bash
   claude plugin marketplace add yatuk/itu-mcp
   claude plugin install itu-mcp@itu-mcp
   ```

Tools that read only public pages (course schedule, archive, academic calendar, library) work without an account.

## Use it

Ask in Turkish or English, for example:

- "Bu hafta teslimim var mı?"
- "Bu dönem hepsinden BB alırsam genel ortalamam kaç olur?"
- "MAT 210E'nin hangi şubelerinde yer var?"
- "Which courses do I still need to graduate, and when are they offered?"

Everything is read-only except two tools, uploading an assignment and saving a registration draft, and both ask you to confirm before anything is sent.

## Data

The server runs on your computer. It sends your İTÜ username and password only to İTÜ's own login page, and reads İTÜ's sites with your session. It also reads two public datasets by course code, with no account data: the course archive at itu-ders.com and a community prerequisite dataset on GitHub. Session cookies, tracking state and downloaded files are stored locally under `~/.ninova_state`. Nothing is sent to the plugin's author and there is no telemetry. What the tools return is passed to Claude as part of your conversation.

Privacy policy: https://github.com/yatuk/itu-mcp#gizlilik-privacy-policy

Source, full documentation and issue tracker: https://github.com/yatuk/itu-mcp
