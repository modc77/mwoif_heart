# V3 Phase 5.3.2 — Safe SEND Recovery

Hotfix หลัง real-server test ของ P5.3.1 ที่ ADD/ACCEPT ผ่าน 20/20 แต่ SEND ผ่านเพียง 3/20.

Changes:
- Add short friend-state settle barrier before SEND.
- SEND uses bounded concurrency instead of the full network worker count.
- Failed SEND is checked against Receiver mailbox before any retry.
- Mailbox-confirmed SEND is treated as committed and is never resent.
- Unknown transport outcome is never blindly retried.
- Only explicit failures that are absent from mailbox are retried with lower concurrency.
- ACCEPT starts at low bounded concurrency because it mutates a single Receiver friend state.
- Batch report now includes send pass diagnostics.
- No DB schema migration.

Defaults (internal, no .env change required):
- friend settle: 1.25s
- SEND workers: up to 10
- SEND retry fan-out: 5 -> 2 -> 1
- mailbox verify delay: 0.45s
