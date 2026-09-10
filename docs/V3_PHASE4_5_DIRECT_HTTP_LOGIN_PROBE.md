# MWOIF HEART V3 — Phase 4.5 Direct HTTP Login Probe

## Scope

Phase 4.5 is an isolated no-browser DevPlay Auth V2 probe.

It does not replace the Phase 3/4 browser login path yet.  The current passed browser provider remains the fallback until this probe returns a full Auth + initMember3 PASS.

## Goal

Test whether Python can do the DevPlay login flow with direct HTTP requests only:

```text
GET app.devplay.com/auth/v2/login-try with LAB V2 query/cookie context
→ submit email form
→ submit password form
→ capture ServerLoginResponse
→ build AuthRecord
→ initMember3
→ get memberSeq + sessionKey
```

## What this patch adds

New commands:

```powershell
.\run.bat http-login-probe-receiver --hj-id 3
.\run.bat http-login-probe-sender --hs-id 3
.\run.bat http-login-probe-pair --hj-id 3 --hs-id 3
```

Recommended first test:

```powershell
.\run.bat http-login-probe-receiver --hj-id 3
```

Only run the pair test after one single account returns `ok=true`.

## PASS criteria

A real PASS must show:

```json
{
  "ok": true,
  "probe": {
    "ok": true,
    "provider": "direct-http",
    "code": "HTTP_LOGIN_CAPTURED",
    "login_bundle": {
      "mid_present": true,
      "refresh_present": true,
      "game_present": true,
      "oven_present": true
    }
  },
  "session": {
    "member_seq_present": true,
    "session_key_present": true
  }
}
```

If login bundle is captured but initMember3 fails, the HTTP login path is only partially passed and must not be promoted yet.

## Expected fail cases

This probe may fail normally with:

```text
NEEDS_BROWSER
LOGIN_PAYLOAD_NOT_FOUND
HTTP_SUBMIT_FAILED
```

Those are not Sender/Receiver account fatal errors.  They mean the DevPlay page probably requires browser JavaScript/WebView execution, redirect state that the generic form submitter cannot reproduce, or another browser-only behavior.

Do not disable sender accounts because this probe fails.

## Secret policy

The probe never prints:

```text
password
raw Cookie
raw Authorization
game/oven/refresh token
sessionKey
raw full URL query
```

It only prints safe URL scheme/host/path, HTTP status, form field names, form counts, cookie names, and redacted field maps.

## Config

Optional `.env` values:

```env
MWOIF_DEVPLAY_HTTP_LOGIN_TIMEOUT_SECONDS=35
MWOIF_DEVPLAY_HTTP_LOGIN_MAX_STEPS=8
MWOIF_DEVPLAY_HTTP_LOGIN_AUTO_SUBMIT=1
```

`state/login_web_context.private.json` is still required because the probe uses the same LAB V2 game-owned web context as the passed browser provider.

## Decision after test

If PASS:

```text
HttpLoginProvider = PRIMARY
BrowserLoginProvider = FALLBACK
```

If FAIL:

```text
Browser/Headless Browser remains PRIMARY
Direct HTTP remains diagnostic only
```
