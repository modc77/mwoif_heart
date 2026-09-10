# M WOIF HEART V3 — Phase 5.0 One-Round Runner

## Scope

Phase 5.0 adds one production-core command that runs a complete Heart round in a single process:

```text
Receiver login once
Sender login once
Add friend
Accept friend
Send heart
Read mailbox
Receive heart
Remove friend
Mark round PASS
```

## Database

This phase does **not** add, remove, or alter database tables/columns.

It uses the existing Phase 1 tables only:

```text
heart_jobs
heart_rounds
heart_events
heart_senders
heart_receivers
```

Runtime execution still writes normal rows/status updates when `--live` is used:

```text
create one heart_rounds row
append heart_events rows
update job/sender/receiver counters/status
```

No schema migration SQL is included in this patch.

## Login provider

Primary login path for the round is:

```text
direct-http-exact-template
```

The replay step does not open a browser. Browser is only used earlier to capture private templates.

Required local private files:

```text
state/http_login_exact_template_receiver_3.private.json
state/http_login_exact_template_sender_3.private.json
```

Do not commit or share those files.

## Commands

Preview only:

```powershell
.\run.bat heart-round-run --hj-id 3 --hs-id 4 --template-hs-id 3
```

Live one full round:

```powershell
.\run.bat heart-round-run --hj-id 3 --hs-id 4 --template-hs-id 3 --live
```

Verbose debug, still redacted:

```powershell
.\run.bat heart-round-run --hj-id 3 --hs-id 4 --template-hs-id 3 --live --verbose-actions
```

## Expected pass

```text
P5 ROUND PASS
"ok": true
"login_counts": {"receiver": 1, "sender": 1}
"browser_during_replay": false
```

## Not included yet

These require a later Phase 5.x database migration:

```text
random sender pool
sender leases
sender+receiver 1 hour cooldown tracking
failed sender replacement
10-worker queue
shared sender password table
production UI
```
