# Telegram Codex Gateway

A Telegram gateway with separate owner profiles, workspaces, native Codex conversations, approvals, scheduled work and durable delivery receipts. Members must be active before ordinary interaction, transcription or inference. A deployment has one administrator; new staff join through invitation, administrator approval, their own login and activation.

This branch is an isolated development candidate. Windows offline native protocol, file-handle, credential and process-lock tests exist. macOS native acceptance, login-session background startup and live provider acceptance remain release blockers; do not infer support from platform code or skipped tests. See [PLATFORMS.md](PLATFORMS.md).

## New installation

Use Python 3.13 and the exact runtime/dependency versions in `versions.json` and `requirements.txt`. Keep source code and private data in separate nonoverlapping local directories. Shared guidance may be empty. No provider account, token or private organization content is included.

1. Run `python -B main.py init --root "C:/GatewayData" --codex "C:/GatewayRuntime/codex.exe" --timezone UTC` on Windows. The bot token is entered through a hidden local prompt. Native macOS initialization remains blocked pending accepted runtime artifact hashes and host tests.
2. Run `python -B main.py serve --config "C:/GatewayData/deployment.json"` for registration polling. Use the short-lived bootstrap command printed by init in a private bot chat.
3. Inspect `main.py pairing-status --config ...` locally. Verify the numeric Telegram ID, then run `main.py confirm-bootstrap --claim CLAIM --owner ID --config ...`. Claiming the token alone grants no administrator access.
4. The pending administrator uses `/login codex`; on the host run `main.py activate-bootstrap --claim CLAIM --config ...` after account readiness. These checks do not start a model turn.
5. For login-session background operation, review `python -B background.py --config ...`, then explicitly install with `--install`. Windows Task Scheduler and macOS per-user LaunchAgent implementations use the deployment ID and owner ID. This installation is separate from native platform acceptance.

All subsequent entry points require the persisted absolute configuration path. The compatibility environment `TELEGRAM_CODEX_ROOT` is for explicit controlled test/migration setups; it never discovers another deployment automatically.

## Telegram commands

- `/invite`, `/members`, `/members approve ID`, `/members activate ID`, `/members suspend ID REASON`, `/members restore ID REASON` are administrator operations. Restored staff confirm `/resume_schedules` for their own paused schedules.
- `/persona`, `/persona propose BASE BODY`, `/persona view ID`, `/persona vote ID HASH approve|reject` manage versioned guidance. `/persona policy BASE admin_only|all_active_members` and `/persona rollback BASE HISTORICAL_VERSION` also require the current approval policy; neither bypasses voting.
- `/mcp status`, administrator `/mcp allow PROVIDER on|off`, and `/mcp connect PROVIDER` manage installed presets. Defaults are off. New tool trust is not inherited. `/login PROVIDER` and `/login_status` use only the caller's permitted configuration.
- Chat, voice, attachments, memory, skills and personal schedules remain owner-bound. Optional offline voice runtime/model files are installed separately under the data root; missing dependencies fail visibly without network model downloads.

Notion has a configuration preset with live OAuth acceptance pending. Google Workspace, Microsoft 365 and Canva presets document their upstream providers and host prerequisites; their presence is not proof of an installed or authorized connector. See `presets/mcp/catalog.json`.

The public scheduler handles reminders, Codex work and registered capabilities. Private adapters and content stay outside this source tree and release manifest. Capability authority comes from the verified actor, never model-supplied owner fields.

## Validation and release

Run `python -B -m unittest discover -s tests -p "test_*.py" -v` only against an explicitly isolated data root. Native tests must use an isolated account home and accepted binary. See [ACCEPTANCE.md](ACCEPTANCE.md), [OPERATIONS.md](OPERATIONS.md), and [SECURITY.md](SECURITY.md).

Profiles share an OS account by design. Gateway authorization is application-level separation, not an OS sandbox for arbitrary local code. Keep data, credentials, database backups and delivery evidence outside this repository. No deployment or remote push is implied by these local changes.
