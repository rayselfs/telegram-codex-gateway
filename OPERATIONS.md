# Operations

## Health and recovery

`Start.ps1` launches `gateway_guard.py`, which owns the Gateway child process and its process tree. The guard retries unexpected exits after 15 seconds, at most three times in a rolling hour, and persists an exhausted state. `stop.request` and `maintenance.request` suppress restarts. It does not kill a busy or hung process because a network check fails. Worker recovery is per owner; its budget resets only after an hour of healthy operation. Model-overload recovery remains separate and bounded: two Sol attempts, two Terra attempts, one Astra attempt, with 15-second waits.

Read fresh `profile/health.json`, not just process presence. `ready` is connection health, not confirmation that an owner's previous task succeeded. `unknown` means inspect native task history and external receipts; do not automatically rerun. The owner can use `/result` to retrieve results (possibly duplicating their delivery) and `/new` to explicitly start a new conversation. Preserve old evidence.

## Delivery and schedules

`outbound_chunks` records each reserved text chunk, digest and Telegram receipt. An ambiguous chunk stays unknown and is not retried automatically. A multi-part answer can be partially delivered; an overall error does not imply no message was sent.

Schedules use the owner's execution lock. Busy jobs wait with a reason until the configured `grace_minutes` deadline (default 15, allowed 1–120), then expire. Execution and notification states are separate. Never rerun an external job just to fix its notification.

Dynamic LINE schedules generate a strict send/skip/error result with native Codex. `preview: true` saves and reports the prepared text without invoking the LINE sender. Preview does not verify that a query was semantically complete or that the source permissions were correct. Review source evidence and content before enabling a real daily send. The sender checks the exact unique destination, verifies a new outgoing message ID and clears the input; uncertainty pauses the schedule.

## Maintenance release

Use `release.py --source STAGED_DIRECTORY --files FILE ...` from the deployment's maintenance environment. It requires fresh idle health, no login or unknown work, no unresolved jobs or notifications, and all owner/scheduler/LINE locks. It snapshots SQLite with the backup API and records code hashes before stopping the exact Gateway process under a maintenance hold. Keep release evidence private.

Copy failures restore previous code. A post-start health timeout requires investigation; it is not an automatic rollback. `release.py --rollback EVIDENCE_DIRECTORY` restores prior existing code through the same idle gates, not databases. New files may remain unused after rollback. Never overwrite live journals with old backups to make statuses look healthy.

Check both owners, schedule delivery and external authorization after a release. Reboot/login startup, live message delivery, and real external-service consent require explicit acceptance; offline tests cannot substitute for them.
