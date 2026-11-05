# telegram-codex-gateway

A Windows Telegram entry point for native Codex agents. Codex handles reasoning, tools, skills, compaction and subagents; this project handles identity routing, Telegram delivery, approvals, scheduling and process recovery. Bot branding is independent of the project name.

This is an initial, deployment-oriented source release, not a turnkey installer or a claim that every production scenario has been tested. See [acceptance](ACCEPTANCE.md), [operations](OPERATIONS.md) and [security](SECURITY.md).

## Features

- Per-owner Codex homes, conversations, workspaces, personal instructions and memory.
- Shared instructions reloaded per turn; version-pinned shared skill packages with author-bound publishing tools.
- Progress icons, replies to the original Telegram message, bounded result delivery and durable per-chunk receipts.
- Per-owner MCP service trust and `/trust audit`; changing service configuration invalidates trust.
- Persistent schedules, owner execution locks, configurable lateness allowance, separate execution and notification records.
- Optional shared LINE account with a browser lease, fixed or dynamically generated text, and preview-only schedules.
- Bounded worker recovery and an independent Gateway exit guard. Ambiguous external writes are not automatically repeated.

## Deployment prerequisites

Windows, a dedicated non-elevated logged-in user, Python 3.13, a Telegram bot token, and a separately installed Codex CLI matching `versions.json` (0.166.0). Use your own Codex authorization; the repository contains no accounts, binaries, browser profiles, third-party skill packages or tokens.

The supported deployment layout remains `D:\CodexTelegram`: put this repository's files in `bridge`, create the Python environment at `runtime\gateway-venv`, and install the pinned requirements there. The Codex executable is expected under `runtime\package\vendor\x86_64-pc-windows-msvc\bin\codex.exe`. Verify its checksum against `versions.json`; do not change the lock merely to suppress a failed check. Python dependency versions are checked at startup too.

Create the directories from `examples/users.example.json`, replace the synthetic IDs with the intended Telegram user IDs, and save it as `profile/users.json`. Copy the generic shared policy to `D:\CodexTelegram\AGENTS.md`. Each owner uses `<home>/codex-home/config.toml` and optionally their own `AGENTS.md`. Configure at least:

```toml
mcp_oauth_credentials_store = "file"
[features]
apps = false
```

Configure each owner's desired MCP servers separately. The Gateway deliberately rejects shared App-account configuration. Run `main.py login --owner YOUR_NUMERIC_ID` with the Gateway Python to log into the corresponding independent Codex home. Never copy credentials from another user or assume DPAPI can move between Windows identities. `Setup-Token.ps1` stores the bot token locally with Windows current-user DPAPI; do not put it into a configuration file or chat.

Use `main.py check --owner YOUR_NUMERIC_ID`, then `Start.ps1`, and inspect fresh `profile/health.json`. Only after validating a manual start should the maintainer register `Install-Schedules.ps1`. It creates per-owner periodic checks and a current-user logon task; it does not provide pre-login service startup. Existing task names cause it to stop instead of replacing them.

Optional LINE requires separate maintenance setup: compatible Chromium, Playwright, a supported LINE extension, shared browser storage, and each owner's `line-browser-config.json`. It is not enabled by cloning this repository. The DOM sender was checked against extension 3.7.2; browser updates require acceptance again. Optional voice requires its own offline model/runtime. Site-specific media and business schedules are intentionally disabled in this distribution.

The public default is `workspace-write`; the originating private deployment explicitly chose broader host-user access. Neither mode makes shared-Windows profiles an OS security boundary. Some tools require additional reviewed configuration. Do not grant broader access just to make a failed test pass.

## Development checks

Use a disposable D-drive root, never an operational profile:

```powershell
$env:TELEGRAM_CODEX_ROOT = 'D:\GatewayTest'
$env:TELEGRAM_CODEX_BINARY = 'D:\path\to\pinned\codex.exe'
python -m unittest discover -s tests -v
```

The root override is for Python tests; the Windows launch scripts and optional browser adapters still use the deployment layout above. Tests skip native protocol-schema verification if the generated schema is absent, and the child-process check if no Codex binary is installed. `tests/test_line_send_flow.mjs` uses local Chromium with all network requests blocked; it also checks the real Windows lease. See its fixed runtime paths before running it.

The copied wire client retains its MIT notice in [LICENSE-codex-wire.txt](LICENSE-codex-wire.txt). A project-wide redistribution license has not yet been selected; public visibility does not grant one. This is an independent project, not an OpenAI product.
