# M WOIF HEART V3 — Phase 5.1 Production Local Foundation

## Scope

Phase 5.1 turns the local tool into the database-backed production prototype that the future web worker can reuse.

This phase **does change the Heart DB schema**, but only by adding new `heart_*` tables. It does not touch `users`, `orders`, `license_*`, `client_*`, or any existing web/license table.

## Added tables

- `heart_prod_migrations`
- `heart_sender_shared_credentials`
- `heart_sender_leases`
- `heart_sender_receiver_cooldowns`
- `heart_job_sender_attempts`
- `heart_job_runtime_locks`

## Runtime policy

- Receiver logs in once per job.
- Sender logs in once per round.
- Primary login provider is `direct-http-exact-template`.
- Browser is not used during replay.
- Sender selection is random, not numeric order.
- Sender is leased while active, so the same sender is not used by another job/worker at the same time.
- Sender + Receiver pair gets 1-hour cooldown after success.
- If a sender account fails at credential/auth/session level, it is moved to Needs Attention and removed from future selection.
- If a sender fails, the job continues and selects a replacement until the requested amount is reached or eligible senders run out.
- If a recovery-sensitive step fails after a send may have happened, the job is paused instead of blindly continuing.

## Apply DB migration

```powershell
.\run.bat prod-schema-status
.\run.bat prod-schema-apply
.\run.bat prod-schema-status
.\run.bat prod-status
```

## Shared sender password

Store one encrypted shared password for the sender pool:

```powershell
.\run.bat sender-shared-password-set --name default
```

Do not send or commit `.env`, password, token, cookie, sessionKey, or any `*.private.json` template.

## Import sender pattern

For existing accounts that all use the same password:

```powershell
.\run.bat sender-import-pattern --prefix mwoifheart --start 1 --end 1000 --width 5 --domain gmail.com --shared-credential default
```

This only imports existing account identities into the local DB. It does not register/create DevPlay accounts.

## Check eligible sender count for a job

```powershell
.\run.bat sender-eligible --hj-id 3
```

The count excludes disabled/error senders, active leases, already-attempted senders for this job, and sender+receiver pairs still in cooldown.

## Production local job run

Preview only:

```powershell
.\run.bat heart-job-run --hj-id 3 --template-hs-id 3
```

Live run with safety cap:

```powershell
.\run.bat heart-job-run --hj-id 3 --template-hs-id 3 --max-rounds 1 --live
```

For a real job, increase `--max-rounds` after the small test passes.

## Notes

Phase 5.1 is intentionally sequential. It establishes lease/cooldown/replacement correctness first. Phase 5.2 can safely add the 10 Sender Workers on top of the same schema.
