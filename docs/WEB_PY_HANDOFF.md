# M WOIF Heart — Web + Python Worker Handoff

เอกสารนี้คือ boundary หลัง Local V1 Freeze สำหรับนำ Core ไปต่อเป็น Web + Python worker

## หลักการ

Web UI ไม่ควรฝัง Heart protocol logic เอง

```text
Web/API
  ↓ create job / query status / request stop
Database
  ↓
Python Worker Pool
  ↓
M WOIF Heart Core
  ├─ Direct HTTP Exact Template Login
  ├─ Warm Sender Pool
  ├─ Friend gRPC
  ├─ Heart DS-v4
  └─ Fast Batch / Adaptive Wave
```

## Core ที่พิสูจน์แล้ว

- `mwoif.application.login_session_manager.LoginSessionManager`
- `mwoif.application.warm_sender_pool.WarmSenderPool`
- `mwoif.application.fast_batch_runner.FastBatchJobRunner`
- `mwoif.storage.repository.HeartRepository`
- `mwoif.storage.vault.CredentialVault`
- `mwoif.application.local_update_guard.LocalUpdateGuard` สำหรับ Lab regression

Web worker ห้าม import `mwoif.ui.*`

## DB contract ที่มีอยู่

Core tables:

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

Production foundation:

```text
heart_sender_shared_credentials
heart_sender_leases
heart_sender_receiver_cooldowns
heart_job_sender_attempts
heart_job_runtime_locks
```

ใช้ DB lease/job lock เป็นตัวกัน worker ชนกัน ไม่ใช้ process memory เป็น source of truth

## Credential contract

- Sender ใช้ shared credential ที่เข้ารหัสใน vault
- Receiver password เป็น temporary per-job credential
- raw password/token/sessionKey ห้ามออก API response และห้ามลง log
- Exact Template เป็น private local/worker file ไม่เก็บใน public web root

## Job semantics

- Receiver login หนึ่งครั้งต่อ Job/session ที่ยัง valid
- Sender ใช้ Warm Pool ก่อน cold login
- Random sender selection + lease
- Sender→Receiver cooldown หลัง heart success จริง
- SEND outcome ไม่ชัด: verify mailbox ก่อน retry ห้าม blind resend
- Web policy เรื่อง quarantine/disable แยกจาก Local; Local Final V1 ไม่ auto-disable Sender

## Worker process recommendation

หนึ่ง process ควรถือ Warm Pool ไว้ต่อเนื่องแทนการเปิด Python ใหม่ทุก Job

Flow:

```text
start worker
→ load config/repo/vault
→ warm sender pool
→ lease queued job
→ receiver preflight
→ run FastBatchJobRunner
→ write realtime/progress events
→ release job lock
→ refill warm pool
```

## ก่อน deploy Web worker หลังเกมอัปเดต

Local Update Guard ต้อง PASS 4/4 ก่อนเสมอ แล้วจึงนำ protocol patch เดียวกันไป worker
