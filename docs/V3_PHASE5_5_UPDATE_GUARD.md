# M WOIF HEART V3 — Phase 5.5 Update Guard

Purpose: keep Local as the operator/Lab verification tool after a game update while the future Web uses the same Core.

## Added
- Warm session lifecycle guard: process-local warmed sessions are recycled before the ~30 minute observed V1 lifetime. Default max age is 1500 seconds (25 minutes) and can be overridden with `MWOIF_HEART_WARM_MAX_AGE_SECONDS`.
- initMember3 bootstrap recovery: direct HTTP login keeps the same authenticated token bundle and retries session bootstrap up to 3 attempts on `SessionBootstrapError`/transport failure before reporting failure.
- Read-only Local Update Check button:
  1. DB/schema health
  2. Direct HTTP Exact Template login
  3. initMember3 session bootstrap
  4. Friend gRPC ListFriends
  5. DS-v4 mailbox read/decode
- Update Check never performs Add/Accept/Send/Receive and never prints raw credentials/tokens/session keys.

## Local policy preserved
- No Sender auto-disable/quarantine.
- Errors remain diagnostics only.
- Pair Sender→Receiver cooldown remains because it represents a confirmed successful heart, not account disablement.

## Version
`3.0.0-phase5.5-update-guard`
