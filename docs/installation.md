# Installation Guide

**İTÜ MCP** is a local MCP server that lets AI assistants read your own İTÜ **Ninova** (LMS) and **OBS** (student portal) accounts through the normal username/password login flow.

## 1. Requirements

- Python 3.11 or newer (`python3 --version` on macOS/Linux, `python --version` on Windows)
- An MCP-compatible client (Claude Desktop, Claude Code, Cursor, Codex CLI, OpenClaw, etc.)

## 2. Install

### Option A0: one-click Claude Desktop extension (.mcpb) — easiest, no terminal

Best for non-technical users. Download the bundle for your platform from the
[latest release](https://github.com/yatuk/itu-mcp/releases/latest),
double-click it, and Claude Desktop walks you through a form for your İTÜ
username and password (the password goes into your OS keychain). The bundle is
fully self-contained — it ships its own Python runtime and every dependency, so
there is **nothing else to install** (no Python, no `pip`, no JSON editing).
Skip the rest of this section if you use this option.

### Option A: pipx (recommended)

[pipx](https://pipx.pypa.io) installs Python CLI tools in isolated environments and exposes their commands on your PATH globally.

```bash
pipx install itu-mcp
```

If you do not have pipx yet:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

On Windows, replace `python3` with `python` or `py -3`.

### Option B: uv

[uv](https://docs.astral.sh/uv/) is a fast Python package manager.

```bash
uv tool install itu-mcp
```

### Option C: pip

```bash
pip install itu-mcp
```

### Option D: from source

```bash
git clone https://github.com/yatuk/itu-mcp.git
cd itu-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After any option, `itu-mcp` should be available on your PATH:

```bash
itu-mcp --help         # CLI help
itu-mcp --version      # package version
itu-mcp --check-auth   # try login and print JSON status
itu-mcp --list-tools   # list MCP tool names
itu-mcp                # start stdio MCP server (what clients launch)
which itu-mcp          # macOS / Linux
where itu-mcp          # Windows
```

(`ninova-mcp` remains as a back-compat alias for the same entrypoint.)

## 3. Smoke test

Run the server directly:

```bash
itu-mcp
```

It is normal for it to wait silently because your AI client talks to it over stdin/stdout. Press Ctrl+C to exit.

## 4. Configure your MCP client

The credentials live in the client's MCP config so the server is launched with them as environment variables. You do not need a `.env` file unless you prefer one.

Username is usually your **İTÜ email** (`name@itu.edu.tr`).

### Claude Desktop / Claude Code

Add this to your Claude MCP config:

```json
{
  "mcpServers": {
    "itu": {
      "command": "itu-mcp",
      "env": {
        "NINOVA_USERNAME": "your.name@itu.edu.tr",
        "NINOVA_PASSWORD": "your_itu_password"
      }
    }
  }
}
```

### Cursor

```json
{
  "mcpServers": {
    "itu": {
      "command": "itu-mcp",
      "env": {
        "NINOVA_USERNAME": "your.name@itu.edu.tr",
        "NINOVA_PASSWORD": "your_itu_password"
      }
    }
  }
}
```

### Codex CLI

```toml
[mcp_servers.itu]
command = "itu-mcp"
env = { NINOVA_USERNAME = "your.name@itu.edu.tr", NINOVA_PASSWORD = "your_itu_password" }
```

Or:

```bash
codex mcp add itu --env NINOVA_USERNAME=your.name@itu.edu.tr --env NINOVA_PASSWORD=your_password -- itu-mcp
```

### OpenClaw / other

```json
{
  "command": "itu-mcp",
  "env": {
    "NINOVA_USERNAME": "your.name@itu.edu.tr",
    "NINOVA_PASSWORD": "your_itu_password"
  }
}
```

## 5. Troubleshooting

- **`itu-mcp: command not found`**: Run `pipx ensurepath` (or restart the shell). If you used a venv, point the MCP config to its absolute script path.
- **Login fails**: Confirm credentials work at https://ninova.itu.edu.tr and https://obs.itu.edu.tr/ogrenci/. Use full **email** username. Optional Playwright: `pipx install "itu-mcp[playwright]"` then `playwright install chromium`.
- **Want a `.env` file**: Create `.env` with `NINOVA_USERNAME` / `NINOVA_PASSWORD`. The server auto-loads it.

## Building the .mcpb bundle (maintainers)

```bash
python scripts/build_mcpb.py
# -> dist/itu-mcp-<version>-<platform>.mcpb
```

## Remote HTTP

See [advanced.md](advanced.md#remote-http-server-chatgpt--claudeai-custom-connectors).

```bash
itu-mcp-remote
```
