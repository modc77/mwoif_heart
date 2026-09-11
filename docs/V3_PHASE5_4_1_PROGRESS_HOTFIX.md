# V3 Phase 5.4.1 — Multi-Batch Progress Hotfix

Fixes an early-completion bug in the Phase 5.4 batch progress commit.

## Root cause
MariaDB evaluates single-table UPDATE assignments left-to-right. The previous batch update incremented `completed_hearts` and then evaluated `completed_hearts + batch_count`, effectively counting the batch twice when deciding whether the job was complete. A 100-heart job could therefore stop at 50/100.

## Fix
- Lock the job row with `SELECT ... FOR UPDATE`.
- Calculate `new_completed` from the pre-update value.
- Write explicit `completed_hearts` and `status` values.
- Apply the same fix to the sequential one-round commit path.
- `resume_job()` repairs legacy partial jobs incorrectly marked `completed`.
- Fast runner only exits when `completed_hearts >= requested_hearts`, not from status text alone.

No database schema changes are required. Existing Job #10 can be resumed from the Jobs page.
