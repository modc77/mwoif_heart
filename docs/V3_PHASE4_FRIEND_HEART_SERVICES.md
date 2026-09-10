# M WOIF Heart V3 Phase 4 — Friend/Heart Services

Scope: port the V1-passed Friend gRPC and Heart DS behavior into V3 service modules while keeping `backup/` read-only.

## What Phase 4 adds

- Friend gRPC request builders:
  - SendFriendRequest
  - HandleFriendRequest / Accept
  - RemoveFriend
  - ListFriends read-only check
- Heart DS v4 services:
  - sendLifeMail2.ds
  - myMailList.ds
  - acceptLifeMail4.ds
- One-step CLI harness:
  - each command logs in Receiver + Sender first
  - then executes exactly one action
  - no full round automation yet
  - secrets are redacted

## Important

Phase 4 is step testing. Phase 5 will build the real state-machine round:

```text
Add -> Accept -> Send -> Mailbox -> Receive -> Remove
```

## Required IDs

Use the demo IDs from Phase 3:

```text
hj_id=3   receiver demo100 job
hs_id=3   sender demo102
```

Adjust IDs if your DB differs.

## Test sequence

First preview one payload/action without network write:

```powershell
.\run.bat friend-add-test --hj-id 3 --hs-id 3
```

Then live, one step at a time:

```powershell
.\run.bat friend-add-test --hj-id 3 --hs-id 3 --live
.\run.bat friend-accept-test --hj-id 3 --hs-id 3 --live
.\run.bat heart-send-test --hj-id 3 --hs-id 3 --live
.\run.bat mailbox-read-test --hj-id 3 --hs-id 3 --live
```

Copy only the `suggested_life_mail_seq` value from mailbox output, then:

```powershell
.\run.bat heart-receive-test --hj-id 3 --hs-id 3 --seq <suggested_life_mail_seq> --live
.\run.bat friend-remove-test --hj-id 3 --hs-id 3 --live
```

## Friend gRPC target

Default comes from V1-pass config:

```env
MWOIF_FRIEND_GRPC_TARGET=gserver.live.prod.devsnova.cloud:443
```

If Friend gRPC fails with `FRIEND_GRPC_TARGET_MISSING`, set this in `.env`.

## Secret rules

Do not send these to chat:

- `.env`
- `state/login_web_context.private.json`
- any password
- raw Cookie
- raw token
- raw sessionKey

Safe to send:

- command output with `secretOutput=NONE`
- response code/message
- `suggested_life_mail_seq`
- redacted status logs
