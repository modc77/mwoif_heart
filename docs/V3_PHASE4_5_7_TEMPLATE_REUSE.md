# M WOIF Heart V3 — Phase 4.5.7 Exact Template Reuse Test

Goal: prove whether one private exact HTTP login template captured from a known-good account can be reused with another owned account without opening a browser again.

This does not modify V1/V2 backups and does not replace the browser fallback yet.

## New commands

Sender reuse test:

```powershell
.\run.bat http-login-template-reuse-sender --template-hs-id 3 --target-hs-id 4
```

Receiver reuse test:

```powershell
.\run.bat http-login-template-reuse-receiver --template-hr-id 3 --target-hj-id 4
```

## Meaning

`--template-hs-id` / `--template-hr-id` selects the existing private template file:

```text
state/http_login_exact_template_sender_<id>.private.json
state/http_login_exact_template_receiver_<id>.private.json
```

`--target-hs-id` / `--target-hj-id` selects the real account to login now.

A pass means the selected template can be reused for another account:

```text
browser=NO
HTTP EXACT REPLAY LOGIN SESSION CAPTURED
SESSION INIT OK
ok=true
```

## Safety

Do not send these files to chat:

```text
state/http_login_exact_template_*.private.json
state/login_web_context.private.json
.env
```

The command output redacts credentials/tokens/sessionKey and prints `secretOutput=NONE`.
