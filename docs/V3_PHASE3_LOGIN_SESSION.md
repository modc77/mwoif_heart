# M WOIF Heart V3 — Phase 3 Login/Auth/Session

Scope: V3 root only. Do not edit backup/v1_legacy_working or backup/v2_login_session_pass.

## What this phase adds

- V3 DevPlay Browser Login adapter using the V2 PASS web context behavior.
- V3 AuthResult/AuthRecord in memory only.
- V3 initMember3 session bootstrap using DS v4 + FastLZ level 2 decoder.
- DB status updates for `heart_receivers` and `heart_senders`.
- CLI tests:
  - `login-test-receiver --hj-id <job>`
  - `login-test-sender --hs-id <sender>`
  - `login-test-pair --hj-id <job> --hs-id <sender>`

## Required local files

`state/login_web_context.private.json` must exist locally. Do not commit it. Do not send it to chat.

`.env` must include `MWOIF_HEART_MASTER_KEY_B64` from Phase 2 and the DevPlay/Game settings if the defaults are not correct.

## Prepare real accounts

Sender credential is persistent:

```powershell
.\run.bat sender-add --label Sender001 --email sender_real@example.com --store-credential
```

Receiver credential is temporary per job:

```powershell
.\run.bat job-create --receiver-email receiver_real@example.com --requested-hearts 1
```

## Tests

```powershell
.\run.bat login-test-receiver --hj-id 1
.\run.bat login-test-sender --hs-id 1
.\run.bat login-test-pair --hj-id 1 --hs-id 1
.\run.bat accounts-list
.\run.bat events-tail
.\run.bat health
```

## PASS criteria

Both receiver and sender show:

- `LOGIN SESSION CAPTURED`
- `SESSION INIT OK`
- `member_seq_present=true`
- `session_key_present=true`
- `secretOutput=NONE`

No raw password, token, cookie, or sessionKey is printed.
