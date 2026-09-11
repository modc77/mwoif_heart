# M WOIF Heart V3 — Phase 5.3 Fast Batch + Real-time Local Engine

## Purpose
Phase 5.3 turns the Local application into the production prototype that the future Web worker can reuse. The UI is only an operator surface; scheduling, leases, cooldowns, Direct HTTP login and Heart/Friend operations remain in Core/Application/Storage layers.

## Runtime architecture

```text
Receiver preflight/login (once per Job)
        |
        +-- Friend capacity check / optional confirmed cleanup
        |
        +-- Fast Batch #1 (up to 50)
        |      +-- random lease Sender accounts
        |      +-- concurrent Direct HTTP sender login
        |      +-- concurrent Add
        |      +-- bounded concurrent Receiver Accept
        |      +-- concurrent Heart Send
        |      +-- one mailbox collection window
        |      +-- multi-seq Receive request
        |      +-- bulk Receiver RemoveFriend cleanup
        |      +-- one DB batch commit
        |
        +-- Fast Batch #2 ... until requested hearts are complete
```

Receiver does not log in again between batches. Each Sender logs in once for the round. Browser is not opened during replay.

## Default Local performance

- Batch size: 50
- Sender login workers: 10
- Network workers: 20
- Pair cooldown: 3600 seconds
- Mailbox attempts: 8
- Mailbox retry delay: 0.35 seconds

The UI also exposes performance profiles:

- `เสถียร`: slots/batch 20, sender workers 5, network workers 10
- `สมดุล`: slots/batch 50, sender workers 10, network workers 20
- `เร็ว`: slots/batch 50, sender workers 20, network workers 40
- `สูงสุด`: slots/batch 50, sender workers 50, network workers 50
- `กำหนดเอง`: manual values

Start with `สมดุล` on real runtime. Move upward only after the server/device remains stable.

## Sender selection / replacement

The existing Phase 5.1 production rules remain authoritative:

- random selection;
- no reuse of the same Sender within a Job;
- DB lease prevents two Jobs using one Sender simultaneously;
- Sender+Receiver pair cooldown is 1 hour after confirmed success;
- fatal account login/auth/session errors disable that Sender and immediately select a replacement;
- transient system/network failures do not disable the Sender account.

## Friend capacity
Before every new/resumed Job, `ListFriends` is checked. If the current batch needs more slots than are free, Local UI asks before deletion. Nothing is deleted when the operator cancels.

After a successful receive batch, the Receiver removes the Sender friendships so the next batch can reuse the slots. If cleanup cannot be confirmed, the Job pauses before another batch starts.

## Mailbox / Receive optimization
Instead of `Mailbox + Receive` once per Sender, Phase 5.3 does:

1. all confirmed Senders send Heart;
2. Receiver polls mailbox for the current batch;
3. sender `memberSeq` is mapped to each `lifeMailBoxSeq`;
4. all found seq values are sent to `acceptLifeMail4.ds` in one request.

The project previously runtime-proved a single seq. Multi-seq is the Phase 5.3 fast path and is intentionally conservative on failure: it re-reads the mailbox, treats disappeared seq values as already committed and retries only seq values still present individually. If a confirmed send cannot be resolved safely, the Job pauses rather than replacing it and risking a duplicate Heart.

## Real-time UI
Core emits compact `@RT` structured events over the existing event callback. The Qt UI consumes these events directly; job progress does not wait for a database refresh.

Live events include:

- Receiver ready
- Friend preflight / cleanup
- Batch start/done
- Sender lease/login/add/accept/send/mailbox/receive/remove
- Job progress, speed and ETA
- Stop-after-current-batch
- Job completed/stopped

Default Local log hides noisy transport/session diagnostics. `Dev Log` shows the technical stream when debugging.

## Stop policy
The Stop button sets `stop_requested=1`. The active batch is allowed to reach its safe boundary, then no new batch is started. This avoids abandoning confirmed sends midway.

## Database
Phase 5.3 introduces **no new schema**. It uses the Phase 5.1 production tables and adds only application methods/transactions.

## Optional environment overrides
No `.env` change is required for Phase 5.3 because the UI passes its current settings directly. These environment values remain supported for non-UI callers:

```env
MWOIF_HEART_FAST_BATCH_SIZE=50
MWOIF_HEART_FAST_SENDER_WORKERS=10
MWOIF_HEART_FAST_NETWORK_WORKERS=20
MWOIF_HEART_FAST_MAILBOX_ATTEMPTS=8
MWOIF_HEART_FAST_MAILBOX_DELAY_SECONDS=0.35
MWOIF_HEART_PAIR_COOLDOWN_SECONDS=3600
MWOIF_HEART_SENDER_LEASE_SECONDS=900
```

Do not put passwords, raw tokens, cookies, session keys or private exact-template JSON in logs, commits or support messages.

## First real-runtime validation
The automated/synthetic paths are checked locally, but two behaviors depend on the live game server response and must be observed on the first Local run:

1. `ListFriends` protobuf parser: UI must show the real friend count/capacity. If parser confidence is insufficient, Phase 5.3 stops before deletion/Sender work.
2. multi-seq `acceptLifeMail4.ds`: if the server does not accept the batch form, the conservative recovery path retries only still-present seq values and pauses on uncertainty.

This is intentional; production safety takes priority over claiming a speed target before the live server proves it.
