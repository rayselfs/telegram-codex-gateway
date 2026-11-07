# Member relay

This is an optional generic Gateway capability. Without `data_root/profile/member-relay.json`, existing registration and chat behavior is unchanged. Actual names, Telegram IDs, aliases and policy belong to deployment data, never source control.

A host administrator supplies a contact directory and explicit directed allowed pairs:

```json
{"contacts":{"101":{"name":"Alex","aliases":["A"]},"102":{"name":"Morgan","aliases":["M"]}},"allowed_pairs":[[101,102],[102,101]]}
```

IDs must be verified, active members. A nonempty display name is already required during registration; Telegram supplies it, so it is not a verified real name. A separate administrator-reviewed relay name and optional aliases are required before a member uses relay. Do not require phone numbers or other unrelated personal information. New members do not automatically receive relay permission. Edit this private configuration atomically and preserve its restricted access; the next request reads it without changing member identity, logins or conversations.

Supported explicit private-chat forms:
- `幫我告訴 Morgan：明天見`
- `發訊息給 M：明天見`
- `/tell Morgan: See you tomorrow`

The recipient receives the sender's configured name plus `委託轉達` and only the provided body. This path does not invoke a model. Other wordings remain ordinary chat; do not assume they have sent anything. Names match exactly after Unicode normalization, trimming and case folding; no substring, surname or fuzzy guess. Ambiguous/unknown names, suspended members, absent profile or disallowed pairs refuse delivery. Forwarded messages and groups do not authorize relay.

One plain-text message, at most 2000 UTF-16 units. No attachments, auto-forwarding, read receipt claim or conversation sharing. A durable reservation in `profile/member-relay.sqlite` precedes the send. Original Telegram sender/message ID binds the operation; changed content on the same ID is rejected. Concurrent/replayed requests do not send twice. An interrupted/ambiguous send remains uncertain and is never automatically retried; manually confirm with the recipient before submitting a new request. A newly submitted message has a new ID and is a new request.

Member relay does not grant administrator rights. Host account file access remains the existing trust boundary; this is not an OS sandbox. Real Telegram delivery acceptance must be performed by authorized users, not by automatic deployment tests.
