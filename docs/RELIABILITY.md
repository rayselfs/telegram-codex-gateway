# Background service fault containment

Per-owner sender, scheduled-result delivery and typing services are contained independently. Unexpected termination records a local fault and is not automatically replayed. Other owners and already-running model/event processing remain alive. The affected owner's new foreground model turns are refused until maintenance; `/status` and health expose the fault. Recovery and availability failures are also contained per owner. Membership revocation is still checked before fault handling, so a fault never bypasses suspension.

There is no blanket restart of failed delivery, no clearing of unknown receipts and no model work replay. Existing separately scheduled workers retain their own safety rules. A controlled maintenance restart may recreate service loops, but existing unknown/sending recovery rules remain in effect; inspect external outcomes first.

`profile/service-errors.jsonl` records time, service label, exception class and up to 12 source file basenames/function names/line numbers. It does not store exception messages, local variables, prompts, response text, account identifiers, URLs or credentials. Shared process fatal errors use the same diagnostic format. Logging failure does not crash another owner. Diagnostics live under deployment profiles, never public source.

This fixes a verified failure-propagation path; it does not prove which exception triggered a historical exit whose details were not retained. OS termination, machine shutdown and failure of shared storage/network remain possible. Healthy-process availability must be confirmed with fresh health and actual process identity, not an old status file.
