# MWOIF Heart V3 — Phase 4.5.5 Direct HTTP Replay Matrix

เป้าหมาย: ทดสอบ no-browser login หลาย shape แบบปลอดภัย หลังจาก Recorder ยืนยัน endpoint จริงแล้ว:

- `POST https://account.devplay.com/v4/checkemail`
- `POST https://account.devplay.com/v3/login/devsisters`

## Command

```powershell
.\run.bat version
.\run.bat http-login-matrix-receiver --hj-id 3
```

ถ้า receiver PASS ค่อยทดสอบ sender:

```powershell
.\run.bat http-login-matrix-sender --hs-id 3
.\run.bat http-login-matrix-pair --hj-id 3 --hs-id 3
```

## Matrix ที่ลอง

ตัวนี้ไม่ได้เดา endpoint ใหม่ แต่ลอง shape ที่ browser/JS อาจสร้างต่างจาก replay เดิม:

- LC: `prefixed`, `json-string-prefixed`, `nested`, `json-string`, `flat`
- `terms_updates`: list, JSON string, map, raw-query, empty, omitted
- header: browser-like/current/no-origin
- body send: compact JSON / requests JSON
- boolean: bool/string
- optional blank fields: include/omit

ค่าทั้งหมดสร้างจาก `login_web_context.private.json` + credential ใน vault เท่านั้น ไม่มีการ forge ค่าใหม่

## Output ที่ต้องดู

ดูแค่บรรทัดนี้พอ:

```text
HTTP MATRIX HIT ...
```

หรือท้าย JSON:

```json
{
  "ok": true,
  "provider": "direct-http-replay-matrix-v1",
  "payload_hit_count": 1,
  "hit_attempt": { ... }
}
```

ถ้าไม่ผ่าน จะเห็น:

```json
{
  "code": "HTTP_MATRIX_NO_HIT",
  "attempt_count": 48,
  "check_pass_count": ...,
  "payload_hit_count": 0
}
```

## Secret policy

ห้าม log หรือส่งออก:

- raw password
- raw cookie
- raw token
- raw sessionKey
- raw request body
- `login_web_context.private.json`

Report ที่ save ใน `logs/http_login_matrix_*.json` เป็น safe summary เท่านั้น

## .env

```env
MWOIF_DEVPLAY_HTTP_MATRIX_MAX_ATTEMPTS=48
MWOIF_DEVPLAY_HTTP_MATRIX_STOP_ON_HIT=1
MWOIF_DEVPLAY_HTTP_MATRIX_SLEEP_MS=150
MWOIF_DEVPLAY_HTTP_MATRIX_SAVE=1
```
