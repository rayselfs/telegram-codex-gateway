# Acceptance record — 2026-11-05

This release publishes a sanitized source snapshot with independent Git history. It does not include operational profiles or a fully configured deployment.

| Area | Evidence and limits |
|---|---|
| Gateway guard | Real local child-exit test, bounded persisted retries, intentional-stop test. Actual long-running outage recovery and reboot not tested. |
| Telegram delivery | Partial success followed by timeout preserves the first receipt and marks the next chunk unknown. Mock transport; no real messages sent. |
| LINE confirmation | Offline Chromium fixture rejects incoming same-text and local pending IDs; real OS lease rejects concurrent access. No real send in this change. |
| MCP audit/trust | Per-owner grant/revoke/configuration-change tests. Actual external-service prompts not exercised. |
| Schedule wait/grace | Default and bounded configurable deadlines tested. No real schedules created. |
| Worker recovery | Healthy-hour reset and initialization input gate tested. No user work replayed. |
| Dynamic preview | Saved/generated strict output and sender-not-called test. Source completeness and real Notion-to-LINE execution remain unverified. |
| Release/rollback | Idle production release and code-hash verification performed in the originating private deployment. Start-process pipe inheritance bug corrected. Full rollback/fault matrix not exercised. |
| Public distribution | Windows Python suite: 112 tests, one protocol-schema check skipped. Native child check used the pinned installed binary. Private superset: 209 tests passed. Existing pipe ResourceWarnings remain. |

The originating service was online after the first release, but one owner had an unresolved dispatch status. Its cause was not proven. The subsequent initialization gate is included in this source release and tested, but was not activated in that service because the idle/unknown deployment gate blocked another restart. This document does not claim full production acceptance.

Before claiming deployment readiness, verify both owners on Telegram, same-service trust and revocation, browser contention, a reviewed dynamic preview, explicitly authorized external delivery, recovery without duplicate writes, staged rollback, and reboot followed by normal-user login. Never run live-message acceptance against real recipients without authorization.
