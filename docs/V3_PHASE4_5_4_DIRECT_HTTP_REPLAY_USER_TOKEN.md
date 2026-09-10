# M WOIF Heart V3 - Phase 4.5.4 Direct HTTP Replay User Token

Patch-only after Phase 4.5.3.

## Why

Phase 4.5.3 made `/v4/checkemail` return `code=20000`, but `/v3/login/devsisters` still returned only `code=40000`.

The recorded browser flow shows `/v3/login/devsisters` sends `user_token`. The replay was sending an empty `user_token` because it did not map `/v4/checkemail.result` into the next request.

## Change

- Use the `/v4/checkemail` token/result in-memory as `user_token` for `/v3/login/devsisters`.
- Do not log the token value.
- Add safe diagnostics only: `user_token_present`, `user_token_len`, `user_token_source`.

## Test

```powershell
.\run.bat version
.\run.bat http-login-replay-receiver --hj-id 3
```

Expected first improvement if this was the missing field:

```text
"user_token_present": true
```

Full pass requires:

```text
"ok": true
"member_seq_present": true
"session_key_present": true
```

No secrets should be printed.
