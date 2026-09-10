# MWOIF Heart V3 Workspace

โครงนี้ถูกจัดใหม่เพื่อเริ่มทำ V3 ที่ root ของ `mwoif_heart` ตามปกติ ไม่ได้เอา V3 ไปซ้อนไว้ในโฟลเดอร์ `v3/`

## โครงสร้าง

```text
mwoif_heart/
├─ backup/
│  ├─ v1_legacy_working/        # V1 เดิมที่ใช้ได้ เก็บไว้เทียบ/ย้อนกลับ
│  └─ v2_login_session_pass/    # V2 ที่ DevPlay login + initMember3 PASS
├─ mwoif/                       # พื้นที่ package หลักสำหรับ V3
├─ state/                       # runtime state ของ V3 ไม่ commit
├─ logs/                        # log ของ V3 ไม่ commit
├─ imports/                     # import ชั่วคราว ไม่ commit
├─ docs/
├─ main.py
├─ run.bat
├─ requirements.txt
└─ .gitignore
```

## กติกา

- ทำ V3 ที่ root นี้เท่านั้น
- `backup/v1_legacy_working/` และ `backup/v2_login_session_pass/` เป็นฐานอ้างอิง ห้ามแก้ตรง ๆ
- ไม่มีไฟล์ private JSON, local credential, token, cookie, sessionKey, `.git`, `__pycache__` ในชุดนี้

## สถานะอ้างอิงล่าสุด

V2 ผ่านแล้วตาม flow:

```text
DevPlay Web Login = PASS
LoginSession Capture = PASS
initMember3 Request = PASS
DS v4/FastLZ Decode = PASS
memberSeq/sessionKey = PASS
login-test = PASS
```
