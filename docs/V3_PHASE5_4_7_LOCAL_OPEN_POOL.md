# M WOIF HEART V3 — Phase 5.4.7 Local Open Pool

Patch-only hotfix for the operator-only Local Control Center.

## Changes
- Local never disables/quarantines a Sender based on historical health state.
- Old `enabled=0`, `health_status=error/disabled`, and `next_available_at` backoff state is normalized before dashboard, Sender list, Warm Pool and job selection.
- Pair Sender→Receiver cooldown remains unchanged because it represents a real successful-send cooldown, not account disabling.
- Warm Pool retries all locally stored/vault-backed Senders; a failed warm attempt stays retryable and is not quarantined.
- Sender page no longer presents an account as "ต้องตรวจสอบ/ปิดใช้งาน". Historical errors are shown only under **Error ล่าสุด** diagnostics.
- Local Sender table displays the full email decrypted in-memory from `heart_sender_vault`. Passwords are never returned/displayed and full email is not written to runtime logs.
- No DB migration.

## Expected startup behavior
After refresh/auto-warm, historical auto-disabled rows are normalized. Sender `พร้อม` should equal the number of stored Sender rows. Warm Pool `ready/target` remains the count of actual valid login+game sessions, so it can be lower than Sender `พร้อม` only when a real warm/login/session attempt has not succeeded yet.
