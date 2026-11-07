# Usage summary

`/usage` shows the caller's recorded token total and an estimated equivalent API Standard price in USD. `/usage all` is restricted to administrators. The command appears in the Telegram menu.

The calculation reads local native session counters without sending model requests. Session IDs and cumulative high-water counters prevent duplicate-file/event counting. Model changes are priced from cumulative deltas. Cached input is discounted; reasoning is already included in output. Long-context rates use the recorded request input size when available. Unknown models, malformed counters or incomplete files suppress the dollar estimate. Sessions without counters do not establish zero usage. Branches may inherit earlier counters.

Rates verified 2026-11-07: https://developers.openai.com/api/docs/pricing . Supported recorded models: gpt-6.1-sol and gpt-6-astra. Standard rates are used regardless of subscription or actual service tier. Cache writes use the 30-minute rate because retention is not recorded. This is a local-history estimate, not an invoice, remaining quota or account-wide total. Detailed assumptions stay here rather than in the chat summary.
