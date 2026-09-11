# V3 Phase 5.3.1 — Adaptive Batch Hotfix

## Why
The first Phase 5.3 live batch showed a burst-failure pattern: a batch of 20 only completed 1 sender, and the failed pre-send accounts were then recorded as attempted for the job. With only a small demo pool this exhausted the remaining eligible senders and paused the job.

## Fix
- ADD keeps the fast parallel pass, then retries only failed items at lower concurrency (5, then 1).
- ACCEPT keeps the fast parallel pass, then retries only failed items at lower concurrency (3, then 1).
- Successful retries clear the previous temporary error state before DB failure marking.
- SEND is intentionally **not** retried automatically because an unknown send outcome could duplicate a heart.
- MAILBOX / RECEIVE safety policy remains unchanged.
- User log now prints concise per-stage counts so the first failing stage is visible immediately.

## No DB migration
This patch changes no schema and no existing cooldown/lease tables.
