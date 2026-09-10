# MWOIF Heart V3 — Phase 4.5.3 Direct HTTP Replay Prefixed LC

Patch-only follow-up after Phase 4.5.2 showed:

- `lc=nested` failed with `LOGIN_PAYLOAD_NOT_FOUND`
- `lc=json-string` still failed with `LOGIN_PAYLOAD_NOT_FOUND`
- Browser network recorder evidence shows DevPlay JS posts `lc` with a longer safe value summary than the replay-generated nested `lc`

This patch adds a new `MWOIF_DEVPLAY_HTTP_LC_MODE=prefixed` mode.

The new mode preserves the original LAB-exported query names inside the POST `lc` value, for example:

```text
lc.anonymous_id
lc.app_build
lc.device.manufacturer
lc.device.model
...
```

No secret values are logged. The replay output now includes only safe shape diagnostics:

```text
lc_shape.type
lc_shape.len
lc_shape.key_count
lc_shape.has_lc_prefix_keys
```

## Required local .env setting

```env
MWOIF_DEVPLAY_HTTP_LC_MODE=prefixed
```

## Test

```powershell
.\run.bat version
.\run.bat http-login-replay-receiver --hj-id 3
```

PASS requires the direct HTTP replay to capture a login bundle, then initMember3 must return memberSeq/sessionKey.

If this still returns only `{code: 40000}`, the next likely blocker is a browser-generated field such as `user_token` or another JS/runtime value. In that case keep Browser/Headless provider as fallback and use the recorder digest before any more replay attempts.
