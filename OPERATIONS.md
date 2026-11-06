# Operations

Every entry point uses the same absolute `deployment.json`. Windows wrappers accept `-Config`; Python commands accept `--config`. Start and Stop request lifecycle changes; inspect private `profile/health.json` for completion. Different deployments have distinct job names and local locks; a same-bot lock additionally prevents competing polling from different data roots under the same host identity.

Background operation requires the host user to be logged in. No pre-login, logout or sleep availability is promised. Due occurrences retain their grace deadlines and receipts after wake; expired work is skipped rather than blindly replayed. Daily/weekly time uses the saved IANA timezone. Nonexistent DST wall times are skipped; the repeated hour does not create a second daily occurrence.

Delivery reservations precede external sends. Unknown results require reconciliation; do not rerun work to repair a notification. Persona notifications retain per-recipient pending/sending/delivered/unknown state and are not resent after ambiguity. `/persona` remains available to read the exact proposal and hash.

Code releases require fresh idle health and all owner execution/worker locks. The code-only release manifest rejects path aliases and traversal. The gateway is asked to stop gracefully before replacement; uncertainty stops the release. Backups are evidence, never an instruction to restore live journals. Partial copy failures restore code; a startup health failure requires inspection.

Rollback must retain current member decisions, new receipts and all post-cutover data. An older version needing users.json must receive an active-only compatibility export generated from the latest registry while workers are stopped. Unsupported new members/capabilities remain disabled. Never restore a historical users.json or database over current authorization and delivery evidence.

Installing or taking over OS background jobs is an explicit host operation. Existing jobs with mismatched commands are not replaced automatically. Migration must record old job names, disable old launchers before enabling replacements, and preserve owner task IDs, next due times and receipts. Native job takeover/wake acceptance is still required before release.
