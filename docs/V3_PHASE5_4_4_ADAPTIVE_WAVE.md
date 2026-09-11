# M WOIF Heart V3 — Phase 5.4.4 Adaptive Wave

Goal: reduce the long ACCEPT/SEND tail seen in P5.4.3 without increasing brute-force concurrency.

## Why this patch exists

The P5.4.3 live benchmark showed local work was already fast (ADD/DB/MAIL/RECEIVE were sub-second to ~1 second), while the 100-sender batch spent most of its wall time inside PIPE + RESCUE. The high-pressure burst caused many receiver friend-state misses, and the end-of-batch rescue path then added a second long barrier.

## Changes

- Turbo batches still use up to 100 senders per batch.
- ACCEPT/SEND side-effect lane is capped to a smaller sweet spot by default:
  - ACCEPT workers: 14
  - SEND workers: 10
- 100 senders are released in waves of 25.
- Waves are staggered by 300 ms instead of firing the full batch as one burst.
- ACCEPT retry is per-sender and rolling; other senders continue progressing while a miss waits for state propagation.
- The old end-of-batch ACCEPT rescue barrier is removed.
- SEND ambiguous outcomes remain mailbox-first and are never blindly resent.
- Explicit SEND failures that are proven absent from mailbox get only one bounded retry wave (max 8 workers), not a 10→5→2→1 serial tail.
- Remaining ACCEPT/SEND failures are released for normal replacement in the next batch.
- Existing batch DB checkpoints, cooldowns, lease rules and recovery safety are unchanged.

## New PERF line

```text
PERF WAVE Batch #1: เตรียม ... | ADD ... | WAVE ... | VERIFY ... | RETRY ... | DBCHK ... | FAILDB ... | FAIL-CLEAN ... | MAIL ... | RECEIVE ... | CLEAN ...
```

The first target is <=30 seconds per 100 successful hearts. This is a live-server benchmark target, not a guaranteed result.

## No schema migration

No database tables or columns are changed.
