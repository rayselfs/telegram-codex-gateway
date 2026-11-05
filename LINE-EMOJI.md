# Native emoji receipt verification

LINE renders native emoji as `img.emoji.native_emoji` elements. Comparing `textContent` directly omits their `alt` text and can mark a successfully sent message as unknown.

The sender now reconstructs literal emoji text on a detached DOM copy and compares the entire message exactly. The same matcher is used for preexisting IDs, waiting for a new message, and collecting the final receipt. It retains outgoing direction, numeric ID, pending-state exclusion, unique new result, unchanged destination/draft and cleared-input checks. Different emoji must still fail equality.

Offline Chromium tests cover native emoji images, existing IDs, changed emoji, incoming messages, pending local IDs and a single send action. A preserved real message DOM also matched its original prepared text exactly with the new matcher, without sending again. The originating deployment applied the fix and reconciled the known false-negative receipt under maintenance locks, preserving backups and its original message ID. No replay or replacement message was sent.

Python regression: private suite 213 passed; public suite 116 with one missing-schema skip. Browser UI and real OS lease checks passed. A subsequent real scheduled delivery using this fix remains to be observed; offline matching is not a claim of external exactly-once delivery.
