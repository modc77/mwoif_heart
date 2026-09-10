# M WOIF Heart V3 — Phase 2 Account + Storage

## Scope

Phase 2 เพิ่ม storage layer สำหรับ Receiver / Sender / Job / Round / Event / Vault เท่านั้น

ยังไม่ทำ:

- DevPlay login จริง
- initMember3 จริง
- Friend gRPC จริง
- Heart DS จริง
- Worker จริง
- UI

`backup/v1_legacy_working/` และ `backup/v2_login_session_pass/` ยังเป็น read-only reference

## Database

ใช้ database เดิมของเว็บ และใช้เฉพาะตาราง `heart_*`

```text
MWOIF_DB_NAME=m_woif
```

ตารางที่ Phase 2 ใช้:

```text
heart_receivers
heart_senders
heart_sender_vault
heart_jobs
heart_receiver_job_vault
heart_rounds
heart_events
heart_worker_runs
heart_settings
```

## Credential policy

Receiver credential เป็น credential ชั่วคราวต่อ Job:

```text
heart_receiver_job_vault
```

เมื่อ Job จบจริง V3 ต้องลบ credential ตรงนี้

Sender credential เป็นบัญชีของเรา ใช้ซ้ำ:

```text
heart_sender_vault
```

ทุก credential ถูก encrypt ด้วย AES-256-GCM และ master key ต้องอยู่ใน `.env` เท่านั้น ไม่อยู่ใน DB และห้าม commit

## Setup vault key

สร้าง key:

```powershell
.\run.bat vault-keygen
```

เอาค่า `value` ไปใส่ใน `.env`:

```env
MWOIF_HEART_MASTER_KEY_B64=ค่าที่ generate ได้
```

ห้ามส่งค่านี้เข้าแชท ห้าม commit `.env`

## Phase 2 commands

```powershell
.\run.bat receiver-add --email receiver@example.com
.\run.bat sender-add --label Sender001 --email sender001@example.com
.\run.bat sender-add --label Sender001 --email sender001@example.com --store-credential
.\run.bat job-create --receiver-email receiver@example.com --requested-hearts 1
.\run.bat accounts-list
.\run.bat jobs-list
.\run.bat events-tail
```

## Safe smoke test with dummy passwords

ใช้สำหรับทดสอบ DB/vault เท่านั้น ไม่ใช้ login เกมจริง

```powershell
.\run.bat storage-smoke --receiver-email receiver.test@example.com --sender-email sender001.test@example.com --sender-label Sender001 --requested-hearts 1 --dummy-secrets
```

ถ้าต้องการทดสอบ policy ลบ credential Receiver หลังจบงานจำลอง:

```powershell
.\run.bat storage-smoke --receiver-email receiver.test@example.com --sender-email sender001.test@example.com --sender-label Sender001 --requested-hearts 1 --dummy-secrets --purge-receiver-vault
```

## PASS criteria

Phase 2 ถือว่าผ่านเมื่อ:

```text
vault-keygen PASS
storage-smoke PASS
accounts-list เห็น receiver/sender
jobs-list เห็น job
health dashboard counter อัปเดต
ไม่มี password/token/cookie/sessionKey หลุดใน output
```

## Next phase

หลัง Phase 2 ผ่าน ค่อยไป Phase 3:

```text
V3 Login/Auth/Session
Receiver A login-test
Sender001 login-test
```
