# M WOIF Heart V3 — Phase 4.5.2 Direct HTTP Replay v2

สถานะ: Probe แยก ยังไม่แทน Browser provider จนกว่าจะ PASS จริง

## หลักฐานจาก Phase 4.5.1

Network Recorder เจอ endpoint จริงตอน Browser login:

```text
POST https://account.devplay.com/v4/checkemail
post_keys=email,lc

POST https://account.devplay.com/v3/login/devsisters
post_keys=agree_ad_day_push,agree_ad_night_push,device_id,device_type,email,lang,lc,oven_access_token,password,push_token,recall_session_id,terms_updates,timezone,user_token
```

Phase 4.5.2 จึงลอง replay endpoint ชุดนี้ด้วย Python HTTP ล้วน โดยไม่เปิด Browser

## คำสั่งทดสอบ

เริ่มจาก receiver ก่อน:

```powershell
.\run.bat http-login-replay-receiver --hj-id 3
```

ถ้าผ่านค่อยลอง sender:

```powershell
.\run.bat http-login-replay-sender --hs-id 3
```

จากนั้นค่อย pair:

```powershell
.\run.bat http-login-replay-pair --hj-id 3 --hs-id 3
```

## PASS criteria

ต้องเห็น:

```json
{
  "ok": true,
  "provider": "direct-http-replay-v2",
  "code": "HTTP_REPLAY_LOGIN_CAPTURED",
  "auth": {
    "refresh_present": true,
    "game_present": true,
    "oven_present": true,
    "device_secret_present": true
  },
  "session": {
    "member_seq_present": true,
    "session_key_present": true
  }
}
```

## ถ้าไม่ผ่าน

ให้ส่งเฉพาะ JSON/log ที่ redacted แล้ว ห้ามส่ง:

- `.env`
- password
- raw cookie
- token
- sessionKey
- `state/login_web_context.private.json`

ค่าที่น่าดูคือ:

```text
stage
code
steps[].step
steps[].http_status
steps[].json.keys
steps[].json.code
```

## Config ใหม่

```env
MWOIF_DEVPLAY_ACCOUNT_BASE_URL=https://account.devplay.com
MWOIF_DEVPLAY_HTTP_CHECKEMAIL_PATH=/v4/checkemail
MWOIF_DEVPLAY_HTTP_DEVSISTERS_PATH=/v3/login/devsisters
MWOIF_DEVPLAY_HTTP_LC_MODE=nested
MWOIF_DEVPLAY_HTTP_WARMUP_GET=1
```

`MWOIF_DEVPLAY_HTTP_LC_MODE` รองรับ `nested`, `flat`, `json-string` สำหรับ debug หาก shape ของ `lc` ไม่ตรงกับ frontend จริง
