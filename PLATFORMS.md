# Platform acceptance matrix

Recorded 2026-11-06, before platform adaptation.

| Target | Minimum selected for this release | Runtime | Acceptance |
|---|---|---|---|
| Windows x64 | Windows 11 | CPython 3.13.7, Codex 0.166.0 (existing pinned Windows hash) | Local offline tests on Windows build 26200; live bot acceptance pending |
| macOS arm64 | macOS 12 | CPython 3.13.7 universal2, Codex 0.166.0 matching architecture | BLOCKED: native host and verified platform artifact/hash unavailable |
| macOS x86_64 | macOS 12 | Same versions, x86_64 binary | BLOCKED: native host and verified platform artifact/hash unavailable |

Only logged-in user operation is in scope. Sleep/logout availability is not promised. This matrix is a test target, not a support claim. Native macOS tests, Keychain access without UI, LaunchAgent environment, file handles, process groups, sleep recovery, and executable mode preservation must pass before release. Windows hashes must never authorize macOS binaries.

Sources checked: [CPython 3.13.7](https://www.python.org/downloads/release/python-3137/) documents universal2 and macOS 10.13 minimum; [Codex installation requirements](https://github.com/openai/codex/blob/main/docs/install.md) state macOS 12+. The latter documentation mentions WSL2 for Windows; this project's native Windows contract is based on its own acceptance evidence, not an inference from that table.
