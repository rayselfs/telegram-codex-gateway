# Dynamic MCP login

`/login NAME` and `/login_status [NAME]` discover permitted, enabled MCP servers in the current owner's Codex configuration. Names are exact configuration keys; disabled, unapproved and other-owner services are not exposed. Existing deployments retain only exact migrated definitions; new connections require an allowed installed preset.

Existing OAuth/bearer authorization or available tools are reused without starting another grant. Status failures do not trigger a blind reauthentication. HTTPS MCP servers can use the pinned Codex CLI's native OAuth flow; stdio, API-key and unsupported providers may require secure local setup instead. This is discovery of configured services, not automatic registration or universal OAuth compatibility.

Authorization URLs come only from the pinned CLI launched for the selected registered service in the owner's home. New providers may use a different authorization host discovered by Codex. HTTPS, state and loopback redirect validation remain mandatory, and callbacks are bound to the active owner's exact state and callback address. Existing Notion/Canva host restrictions remain. Callback codes never enter model input; temporary Telegram callback messages are deleted best-effort as in the existing flow.

The login subprocess holds the owner's execution lease. Completion reloads tools but does not replay earlier operations. `/login_cancel` stops the flow without deleting existing credentials. These Gateway commands do not require a new conversation after deployment.

Offline tests cover owner boundaries, disabled services, paginated status, existing-login reuse, callback isolation and lease cleanup. Real provider OAuth, logout/relogin and phone callback acceptance remain separate requirements. No stored authorization is implied by a preset or passing mock tests.
