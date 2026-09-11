# M WOIF Heart V3 — Phase 5.4.3 Rescue + Batch DB

Purpose: remove the hidden latency observed in Turbo 100 jobs and recover transient ACCEPT misses before burning senders.

## What the benchmark showed

In the 100-sender Turbo batch, the network pipeline itself completed in ~11 seconds, but the batch did not enter MAILBOX for ~28 seconds afterwards. The old runner performed per-sender MariaDB writes for every ACCEPT failure and every confirmed SEND checkpoint. With dozens of misses this opened hundreds of DB connections and dominated wall-clock time while remaining invisible in the previous PERF line.

## Changes

- Batch DB checkpoint for confirmed SEND rounds.
- Batch DB persistence for failed rounds/attempts/senders and lease release.
- Batch cooldown writes for recovery-required senders.
- Turbo ACCEPT rescue wave before a sender is finalized as failed.
- Rescued ACCEPT senders are sent through duplicate-safe verified SEND recovery.
- Existing mailbox-first duplicate protection is retained.
- PERF log now exposes RESCUE, DBCHK, FAILDB, FAIL-CLEAN and final CLEAN timings so there is no hidden wall-clock gap.

## No schema migration

No table/column is added or removed. This patch uses the existing Phase 5.1 production tables.

## Expected log

```
TURBO ACCEPT: ...
TURBO RESCUE: ACCEPT ค้าง ...
TURBO RESCUE: ACCEPT รวม ... • SEND รวม ...
MAILBOX: ...
RECEIVE: ...
PERF TURBO Batch #1: ... PIPE ... | RESCUE ... | DBCHK ... | FAILDB ... | FAIL-CLEAN ... | ...
```

The next real benchmark should use a new receiver or senders not in pair cooldown and preferably 100+ warm senders.
