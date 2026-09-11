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


## Local V1 Final Freeze (Phase 5.6)

Current frozen Local/Lab release:

```text
M_WOIF_HEART_LOCAL_V1
3.0.0-phase5.6-local-v1-final
```

Local is the regression/reference tool used after a game update. The customer-facing production flow moves to Web + Python workers and must not import `mwoif.ui.*`.

Final local preflight:

```powershell
.\run.bat version
.\final_check.bat
```

After a game update, open Local and run **ตรวจระบบ**. All four Update Guard stages must PASS before propagating protocol changes to the Web worker. See `docs/LOCAL_UPDATE_RUNBOOK.md` and `docs/WEB_PY_HANDOFF.md`.
