# Dynamic MCP login

`/login NAME` and `/login_status [NAME]` discover enabled MCP servers in the current owner's Codex configuration. Newly registered services do not need another Gateway provider entry. Names are exact configuration keys; disabled and other-owner services are not exposed.

Existing OAuth/bearer authorization or available tools are reused without starting another grant. Status failures do not trigger a blind reauthentication. HTTPS MCP servers can use the pinned Codex CLI's native OAuth flow; stdio, API-key and unsupported providers may require secure local setup instead. This is discovery of configured services, not automatic registration or universal OAuth compatibility.

Authorization URLs come only from the pinned CLI launched for the selected registered service in the owner's home. New providers may use a different authorization host discovered by Codex. HTTPS, state and loopback redirect validation remain mandatory, and callbacks are bound to the active owner's exact state and callback address. Existing Notion/Canva host restrictions remain. Callback codes never enter model input; temporary Telegram callback messages are deleted best-effort as in the existing flow.

The login subprocess holds the owner's execution lease. Completion reloads tools but does not replay earlier operations. `/login_cancel` stops the flow without deleting existing credentials. These Gateway commands do not require a new conversation after deployment.

Validation includes dynamic registration, owner boundaries, disabled services, paginated status, existing-login reuse, callback isolation and lease cleanup. In the originating deployment, a read-only XMind check found OAuth authorization and 35 tools, and the new `/login xmind` handler did not start a grant. Existing configuration and credential-file hashes remained unchanged. No external XMind operation or new login was performed. Actual logout/relogin and a new provider's phone callback remain untested.
