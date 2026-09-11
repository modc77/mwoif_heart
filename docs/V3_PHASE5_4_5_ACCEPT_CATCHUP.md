# M WOIF Heart V3 — Phase 5.4.5 Accept Catch-up

## Goal
Keep the P5.4.4 Adaptive Wave speed while recovering transient receiver-side ACCEPT misses inside the same batch instead of consuming fresh Sender accounts immediately.

## Why
P5.4.4 live runs showed ~52–53 ACCEPT/SEND successes from 100 Sender attempts in about 15 seconds. The remaining Sender accounts were marked failed for the job, so the next batch consumed new Sender accounts until the job exhausted all unique accounts. Warm Pool READY is receiver-agnostic and is not the same as job/receiver eligibility.

## Changes
- Keep Adaptive Wave 25 / ACCEPT 14 / SEND 10 initial lane.
- Add two delayed ACCEPT catch-up waves for the same already-added Sender accounts.
- Catch-up uses bounded receiver pressure: 8 workers, then 6 workers.
- A recovered ACCEPT immediately continues to SEND.
- SEND ambiguity still uses mailbox verification; unknown outcomes are never blindly resent.
- Only Sender accounts that still fail ACCEPT after catch-up are marked failed/replaced.
- Add `CATCHUP` timing to PERF WAVE logs.
- Return end-of-run eligible/cooldown/attempted counts.
- Improve Local UI no-eligible warning so Warm Pool is not confused with receiver eligibility.

## No changes
- No database migration.
- No V1/V2 changes.
- No Login/Auth/Template change.
- No cooldown policy change.
- No raw secrets in logs.

## Benchmark guidance
For a clean 100-heart benchmark, request exactly 100 hearts and use at least 100 receiver-eligible Sender accounts. If only 151 unique Sender accounts exist, a request for 155 hearts cannot complete for one receiver within the 1-hour pair cooldown even at 100% success.
