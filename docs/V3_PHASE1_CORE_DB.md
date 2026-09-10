# M WOIF Heart V3 — Phase 1 Core/DB

## Scope

Phase 1 สร้างเฉพาะฐาน Core + Database bootstrap เท่านั้น

- ไม่แตะ `backup/v1_legacy_working/`
- ไม่แตะ `backup/v2_login_session_pass/`
- ยังไม่ทำ Login/Auth/Session จริง
- ยังไม่ยิง Friend/Heart API
- ยังไม่สร้าง UI

## Database

V3 ใช้ database เดิมของเว็บ:

```text
mwoifmod_license_keys09
```

และใช้เฉพาะตารางที่ขึ้นต้นด้วย `heart_`:

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

## Commands

```powershell
run.bat version
run.bat config
run.bat db-test
run.bat schema-status
run.bat settings-init
run.bat health
```

## Expected first test

หลัง import SQL แล้วให้รัน:

```powershell
cd /d E:\xam\htdocs\mwoif_heart
copy .env.example .env
run.bat schema-status
run.bat settings-init
run.bat health
```

ถ้าผ่านต้องได้:

```json
{
  "ok": true,
  "missing": [],
  "secretOutput": "NONE"
}
```

## Secret policy

Log/CLI output ต้องไม่แสดง password, token, cookie, sessionKey, deviceSecret แบบเต็ม

ค่าที่เกี่ยวกับ secret แสดงได้แค่:

```text
present / len / fp / secretOutput=NONE
```
