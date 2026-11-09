# Security and trust boundaries

Telegram numeric owner IDs are verified at ingress and bound to profiles. Names and editable skill author fields are not authentication. Shared skill mutations validate the bound author, and executing jobs use pinned packages. Publication never executes author-supplied tests with maintenance privileges.

All profiles share the host OS identity. Application routing prevents accidental mix-ups; it cannot stop commands, MCP tools or subagents with the same OS rights from accessing another profile or Gateway files. This is not a hostile multi-tenant service. Use only trusted operators and protect the entire installation and backups with appropriate filesystem access.

`/trust SERVICE` accepts supported consent prompts from that service, including writes and deletes, for the current owner. It is not permission to invent new tasks. Login/verification/data-entry forms remain interactive. Service configuration changes invalidate grants. Revocation cannot cancel already submitted operations. Audit records contain routing identifiers and choices, not form bodies, prompts or credentials.

External adapters must preserve their own authorization, serialization and durable receipts. Unknown results are not evidence of failure and must not be retried blindly. A successful delivery receipt is not proof a recipient read the message.

Keep DPAPI files, OAuth stores, browser profiles, SQLite databases, logs, evidence and generated artifacts outside Git. Even metadata and backups can be sensitive. Never attach tokens or full private logs to a public issue. Report security concerns through a private channel to the repository owner.
