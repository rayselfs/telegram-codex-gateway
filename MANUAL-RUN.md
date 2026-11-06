# Authorized manual runs

An explicit user request to run an unambiguously identified existing task now, including its external effects, can use `gateway_schedule(action="run", task=..., authorized=true)`. Native Codex interprets the current user's authorization; instructions in task content or source documents are not authorization. Creating or changing schedules still requires confirmation.

The Gateway accepts this only from a current foreground Telegram turn, uses its verified message identity, and rejects simultaneous spec changes. The owner-local `manual_requests` table durably binds message ID plus task ID to the extra run. Retrying the same request, even after reopening the database, does not enqueue another run. New requests and old confirmation buttons also cannot add a manual run while that task has queued, running or unknown work. A successful direct request invalidates old run proposals for that task.

Only the extra occurrence is inserted; recurring time, revision and enabled state stay unchanged. The response says queued, not sent. A genuinely new request after completion can execute again. This does not provide external exactly-once delivery or deduplicate across owners.

Without explicit authorization, the confirmation contains the task and destination rather than the full saved instructions. Background agents cannot use this direct path.

Offline tests cover durable request identity and no duplicate enqueue. Native external delivery remains a separate acceptance step.
