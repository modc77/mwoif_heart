# M WOIF HEART V3 — Phase 4.5.1 Network Recorder

## Goal

Phase 4.5.0 proved that simple `requests/httpx` form submit is not enough:

- `GET /auth/v2/login-try` returns `200`
- classic HTML `<form>` is not available in the raw HTTP page
- the login input is created by the browser/JavaScript surface

This patch adds a one-time safe recorder to capture the **real network shape** used by the working browser login, so the next patch can attempt Direct HTTP replay against the actual endpoint instead of guessing.

## Important rule

This recorder is not the final no-browser login provider. It uses the already-working Browser provider once, but records only safe metadata:

- method
- safe URL path without query/fragment
- status code
- content-type
- request header names
- POST field names with redacted values
- response JSON top-level keys
- DOM signals such as input type/placeholder/name presence
- endpoint hints from loaded JS chunks

It must not write:

- raw email/password
- raw Cookie header
- raw token
- auth code/state/sessionKey
- raw redirect URL query
- raw response body

## Commands

Receiver:

```powershell
.\run.bat http-login-record-receiver --hj-id 3
```

Sender:

```powershell
.\run.bat http-login-record-sender --hs-id 3
```

Expected success shape:

```json
{
  "ok": true,
  "receiver": {
    "record": {
      "ok": true,
      "provider": "browser-network-recorder",
      "code": "NETWORK_LOGIN_CAPTURED",
      "report_path": "...logs\\http_login_network_record_receiver_3_....json",
      "endpoint_hints": []
    },
    "session": {
      "member_seq_present": true,
      "session_key_present": true
    }
  },
  "secretOutput": "NONE"
}
```

## What to send back for the next step

Send the terminal output around:

- `NETWORK REQUEST ... post_keys=...`
- final JSON `record.events_tail`
- final JSON `record.endpoint_hints`
- `record.dom_signals`

The report file in `logs/` is designed to be safe, but still review before sharing. Never send `.env`, password, raw cookie, raw token, sessionKey, or `state/login_web_context.private.json`.

## Next decision

If the recorder shows a stable endpoint and field names:

```text
Phase 4.5.2 Direct HTTP Replay v2
```

If it shows captcha/challenge or opaque JS-only browser state:

```text
Use Headless Browser provider as production fallback
```
