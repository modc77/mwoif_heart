# Local Update Runbook

ใช้เอกสารนี้ทุกครั้งที่เกมอัปเดต version/build หรือ server protocol เปลี่ยน

## 1. ก่อนแตะโค้ด

```powershell
.\run.bat version
.\final_check.bat
```

`local-final-check` ต้องไม่แสดง secret และไม่ทำ game action

## 2. Update Guard

เปิด Local UI แล้วกด **ตรวจระบบ**

Expected:

```text
DB                 PASS
LOGIN/SESSION      PASS
FRIEND gRPC        PASS
MAILBOX/DSv4       PASS
UPDATE CHECK PASS 4/4
```

## 3. ถ้า FAIL

- `DB` — ตรวจ schema/connection ก่อน ห้ามไล่ network
- `LOGIN/SESSION` — ตรวจ Login Web Context, Exact Template, initMember3
- `FRIEND_GRPC` — ตรวจ target/metadata/protobuf/request route
- `MAILBOX_DS_V4` — ตรวจ DS-v4 request/response/FastLZ route

อย่าแก้ layer ที่ยัง PASS

## 4. หลังแก้

- Update Guard ต้องกลับมา PASS 4/4
- ทดสอบ Heart งานเล็กก่อน
- ทดสอบ Fast Batch 100 เมื่อจำเป็น
- ตรวจว่า Local ยังไม่ auto-disable Sender
- ตรวจ pair cooldown เฉพาะ Sender→Receiver ที่ส่งสำเร็จจริง

## 5. Secret policy

ห้ามเอาไฟล์ต่อไปนี้ขึ้น Git/ZIP/แชท:

```text
.env
state/*.private.json
*.token.json
*.session.json
raw password
raw token/cookie/sessionKey
```

ให้ log เฉพาะ status, length, fingerprint, expiry และ error code ที่จำเป็น
