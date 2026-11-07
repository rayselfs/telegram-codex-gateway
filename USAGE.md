# Local token usage summary

`/usage` (or `/usage self`) shows the active member's recorded usage. `/usage all` requires an active administrator and lists registered members, including retained history for suspended members. Authorization is checked before file access and before returning results. No arbitrary member IDs, paths or file contents are accepted or displayed.

This command reads only `codex-home/sessions/*.jsonl` recursively within each registered member's profile. It does not read credentials, make provider requests, invoke models, or return conversation text. Parsing happens off the chat event loop. Identical session IDs are merged; cumulative token counters use per-field high-water values, never the sum of repeated cumulative events.

Fields: input, cached input, output, reasoning output and total tokens. Cached/reasoning values are components, not extra charges to add to total. Counts cover locally retained session records only. Missing files, invalid entries, decreasing counters and coverage gaps are labeled; absent records do not mean zero usage. Forks can include inherited cumulative counts. Do not equate this summary to metered consumption, money, an account-wide balance, remaining quota or a daily/weekly billing report. The scan is capped at 256 MiB per member and 8 MiB per line; skipped files are labeled incomplete.

The generic code contains no real names, IDs or usage data. Private member registry names and histories stay under the configured data root. Reading these counters does not itself use model tokens. OS-level file access remains the existing host trust boundary.
