# M WOIF Heart V3 — Phase 4.5.6 Exact Login Template

สถานะ: research probe เท่านั้น ยังไม่แทน Browser provider หลักจนกว่าจะ PASS จริง

## เป้าหมาย

ลองด่านสุดท้ายของ Direct HTTP/no-browser:

1. เปิด Browser ที่ผ่านแล้ว 1 ครั้ง
2. จับ request จริงของ `/v4/checkemail` และ `/v3/login/devsisters`
3. สร้าง private template ไว้ใน `state/` โดยแทนค่า secret ด้วย placeholder
4. Replay ด้วย Python HTTP ล้วน โดยไม่เปิด Browser
5. ถ้าได้ ServerLoginResponse แล้วส่งต่อ `initMember3`

## คำสั่งทดสอบ

Capture template ตัวรับ:

```powershell
.\run.bat http-login-template-capture-receiver --hj-id 3
```

Replay template ตัวรับแบบไม่เปิด Browser:

```powershell
.\run.bat http-login-template-replay-receiver --hj-id 3
```

ถ้าตัวรับผ่าน ค่อยทำตัวส่ง:

```powershell
.\run.bat http-login-template-capture-sender --hs-id 3
.\run.bat http-login-template-replay-sender --hs-id 3
```

ถ้าทั้งสองผ่าน:

```powershell
.\run.bat http-login-template-replay-pair --hj-id 3 --hs-id 3
```

## ไฟล์ private

ไฟล์ template จะอยู่ประมาณนี้:

```text
state/http_login_exact_template_receiver_3.private.json
state/http_login_exact_template_sender_3.private.json
```

ห้ามส่งไฟล์นี้เข้า chat และห้าม commit ถึงแม้ระบบแทนค่า secret เป็น placeholder แล้วก็ตาม

## PASS criteria

ต้องเห็น:

```text
HTTP EXACT REPLAY LOGIN SESSION CAPTURED
SESSION INIT OK
```

และ JSON ท้ายต้องมี:

```json
{
  "ok": true,
  "session": {
    "member_seq_present": true,
    "session_key_present": true
  }
}
```

## FAIL criteria

ถ้ายังได้:

```text
LOGIN_PAYLOAD_NOT_FOUND
```

หลัง replay exact template แปลว่า Direct HTTP ยังขาด browser/runtime state ที่ copy เป็น template ไม่พอ ให้หยุดสาย Direct HTTP ชั่วคราวและใช้ Headless Browser เป็น provider หลัก
