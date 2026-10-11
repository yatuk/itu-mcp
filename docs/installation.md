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

### Option A: pipx and three commands (recommended)

[pipx](https://pipx.pypa.io) installs Python command line tools in their own
environment and puts the command on your PATH.

```bash
pipx install "itu-mcp[keyring]"   # 1. install
itu-mcp setup                     # 2. type your İTÜ username and password once
itu-mcp connect                   # 3. add the server to Claude Desktop / Claude Code / Cursor
```

Then quit and reopen your client. That is all: there is no file to edit and no
password to paste anywhere.

What the three commands do:

1. `pipx install "itu-mcp[keyring]"` installs the server together with the
   `keyring` package, which talks to the password store of your operating system
   (Windows Credential Manager, macOS Keychain, GNOME Keyring / KWallet on Linux).
2. `itu-mcp setup` asks for your İTÜ username (the e-mail, such as
   `name@itu.edu.tr`) and your password. The password is not shown while you
   type. Both are saved in the OS keychain, not in a file. It then offers to try
   the login once, and to run step 3 for you. Flags: `--no-verify` skips the login
   attempt, `--yes` accepts the default answers, `--lang en` prints English.
3. `itu-mcp connect` registers the server in the clients it finds, with the full
   path of the `itu-mcp` program and **no credentials** in the config. Name one
   client to limit it: `itu-mcp connect claude-desktop`, `claude-code` or
   `cursor`. `--dry-run` shows the change without writing. An existing config is
   parsed first, only the `mcpServers.itu` entry is changed, and a timestamped
   backup (`<file>.bak-YYYYMMDD-HHMMSS`) is written next to it. A config that is
   not valid JSON is left untouched and the block to add by hand is printed.

Other commands:

```bash
itu-mcp doctor            # one line per check, with the fix for anything that is off
itu-mcp doctor --online   # also tries a login and looks up the newest version
itu-mcp logout            # delete the stored session cookies
itu-mcp logout --forget-password   # also remove username and password from the keychain
```

If `setup` says no keychain is available (a server without a desktop session,
for example), it prints the block for the manual path in section 4 instead.

If you do not have pipx yet:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

On Windows, replace `python3` with `python` or `py -3`, and open a new terminal
after `ensurepath`.

### Option B: uv

[uv](https://docs.astral.sh/uv/) is a fast Python package manager.

```bash
uv tool install "itu-mcp[keyring]"
```

### Option C: pip

```bash
pip install "itu-mcp[keyring]"
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
itu-mcp setup          # store username and password in the OS keychain
itu-mcp connect        # register the server in your MCP client
itu-mcp doctor         # check the installation
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

## 4. Manual configuration (the alternative)

Skip this section if `itu-mcp setup` and `itu-mcp connect` worked.

Without the keychain, the credentials live in the client's MCP config and the
server is started with them as environment variables. The password is then
stored in plain text in that file (see [security.md](security.md#where-the-password-lives)).
You do not need a `.env` file unless you prefer one.

Username is usually your **İTÜ email** (`name@itu.edu.tr`).

The server looks for credentials in this order: process environment (the `env`
block below), the `.env` file, then the OS keychain. Values set here therefore
win over the ones saved by `itu-mcp setup`.

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

- **Not sure what is wrong**: Run `itu-mcp doctor`. Each line is marked OK, UYARI (warning) or HATA (error), and anything that is not OK comes with the command that fixes it.
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
