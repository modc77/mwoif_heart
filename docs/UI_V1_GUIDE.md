# Heart Worker V1 — UI Workflow

## Goal

UI เป็น entrypoint หลักทั้งหมดของ Heart Worker V1

```text
run.bat -> main.py -> UI
```

ไม่มีขั้นตอนปกติที่ต้องพิมพ์ command ใน CMD

## Receiver / Sender model

- Receiver: 1 slot (default A)
- Senders: arbitrary slots 1–100++
- Sender ทุกตัววิ่งหา Receiver ตัวเดียว
- ไม่ทำ A↔B สลับกันอีก

## Per-sender state

UI แสดง:

```text
QUEUED
RUNNING
DONE
FAILED
```

และ current step:

```text
เพิ่มเพื่อน
รับเพื่อน
ส่งใจ
อ่าน Mailbox
รับใจ
ลบเพื่อน
```

## V1 execution policy

Sequential only.

เหตุผล: flow ทั้ง 6 ขั้น Live ผ่านแล้ว จึง freeze baseline ก่อนทำ optimization/parallelism

## Future Login integration

เมื่อ Email/Password login ผ่าน:

```text
UI Add Account
  -> email/password
  -> login/materialize
  -> write .state/session_SLOT.json
  -> write .state/auth_SLOT.json
  -> account READY
```

ส่วน workflow ส่งใจไม่ต้องเปลี่ยน
