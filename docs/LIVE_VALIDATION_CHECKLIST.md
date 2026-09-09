# Live Validation Checklist

ใช้บัญชีทดสอบคู่เดียวก่อนเปิด batch 1–100++

## Phase A — Read only

1. Import Session/Auth ใหม่ของ Receiver และ Sender
2. `python main.py doctor`
3. `python main.py accounts`
4. `python main.py friend-list <sender>` ต้อง `ok=true`
5. `python main.py mailbox <receiver> <sender> --live` ต้อง HTTP 200 / COMPLETE และ decode ได้

ถ้า Phase A ไม่ผ่าน ห้ามทำ Write

## Phase B — Write ทีละขั้น

```text
1 ADD
2 ACCEPT
3 SEND HEART
4 MAIL LIST -> seq
5 RECEIVE HEART
6 REMOVE FRIEND
```

เช็กทุก step ว่า response success จริง ไม่ใช่แค่ transport 200

### gRPC

ต้องไม่มี:

```text
UNKNOWN
Application error processing RPC
UNAUTHENTICATED
```

### DS

ต้องเห็น:

```text
http_status=200
response_code=200
response_message=COMPLETE
```

หลัง Receive ให้อ่าน mailbox ซ้ำและยืนยันว่า seq เดิมหาย

หลัง Remove ให้อ่าน Friend list เพื่อตรวจว่าความสัมพันธ์ถูกลบ

## Phase C — Cycle

เมื่อทีละขั้นผ่าน:

```powershell
python main.py cycle 1 --receiver A --live
```

## Phase D — Small batch

```powershell
python main.py batch 1-3 --receiver A --live
```

ผ่านค่อยเพิ่ม 10, 20, 100+.

อย่าเริ่ม optimize ความเร็วพร้อมกับ update compatibility เพราะจะแยกสาเหตุ error ยาก
