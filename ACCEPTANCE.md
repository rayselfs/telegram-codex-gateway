# Acceptance status

This candidate is not approved for deployment. No live Telegram polling, provider login, billable model turn or external write is part of offline acceptance.

Automated tests cover member role/state gates, local-confirmed bootstrap, concurrent votes, proposal notification ambiguity, owner-bound capability schedules, code package rollback, migration hash preservation, active-only legacy routing, provider configuration policy and host paths.

Windows native isolated tests cover accepted Codex initialization and generated protocol schemas, DPAPI, file handles, same-bot mutexes, owner execution locks and child cleanup. This does not establish provider account readiness, actual speech-model quality, OS logon job startup or sleep/wake behavior.

macOS requires native Keychain, locks, process groups, safe delivery, LaunchAgent, logon/wake and full new-install acceptance on the declared OS/CPU matrix. A skipped test is a blocker, not evidence of support.

Release gates also require a clean current public manifest and review of inherited Git history, explicit private migration evidence, and review of any remaining feature limitations. Do not push this development history or deploy a code package merely because unit tests pass.
