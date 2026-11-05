# Authorized manual runs

An explicit user request to run an unambiguously identified existing task now, including its external effects, can use `ezra_schedule(action="run", task=..., authorized=true)`. Native Codex interprets the current user's authorization; instructions in task content or source documents are not authorization. Creating or changing schedules still requires confirmation.

The Gateway accepts this only from a current foreground Telegram turn, uses its verified message identity, and rejects simultaneous spec changes. The owner-local `manual_requests` table durably binds message ID plus task ID to the extra run. Retrying the same request, even after reopening the database, does not enqueue another run. New requests and old confirmation buttons also cannot add a manual run while that task has queued, running or unknown work. A successful direct request invalidates old run proposals for that task.

Only the extra occurrence is inserted; recurring time, revision and enabled state stay unchanged. The response says queued, not sent. A genuinely new request after completion can execute again. This does not provide external exactly-once delivery or deduplicate across owners.

Without explicit authorization, the confirmation contains the task and destination rather than the full saved instructions. Background agents cannot use this direct path.

Validation: private suite 213 passed; public suite 116 with one missing-schema skip. Fake-data tests cover authorization gating, compact confirmation, durable duplicate prevention, stale buttons, pending/unknown jobs and unchanged recurring settings. No live job or external send was triggered. The originating deployment has now activated this patch after both owners were idle, with code hashes and new-process health verified. Shared policy was updated too. Existing conversations need a new conversation to load the dynamic-tool schema. No real job or send was initiated during deployment.

Release checks now distinguish preserved historical unknown jobs from the selected conversation. All active jobs still block release; the selected thread's unknown status and pending deliveries still block. Historical evidence is neither cleared nor replayed. A missing selected thread fails closed. This change removes a maintenance deadlock without asserting that an old uncertain external operation succeeded or failed.
