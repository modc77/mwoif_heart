# M WOIF Heart V3 — Phase 5.4 Performance Engine

Version: `3.0.0-phase5.4-performance-engine`

## เป้าหมาย

Phase 5.4 เอา login/initMember3 ของ Sender ออกจาก critical path ของลูกค้าให้มากที่สุด และลดค่า TCP/TLS / gRPC setup ที่ถูกจ่ายซ้ำในทุก request โดยยังคงกฎจาก P5.1–P5.3 ทั้งหมด ได้แก่ random sender, lease, cooldown 1 ชั่วโมงต่อ Sender+Receiver, replacement, safe SEND recovery, batch mailbox/receive และ friend-capacity preflight

เป้าหมาย benchmark คือไล่ไปหา `100 ใจ <= 15 วินาที` แต่ต้องวัดจาก server จริง ไม่ hard-code หรือรับประกันค่าความเร็วล่วงหน้า

## สิ่งที่เพิ่ม

### Warm Sender Session Pool

- Login Sender + `initMember3` ล่วงหน้าใน background
- ใช้ `direct-http-exact-template` เท่านั้น (`browser=NO`)
- เก็บ Auth + GameSession ใน memory เท่านั้น ไม่เขียน raw token/sessionKey ลง DB/log
- UI มีการ์ด `พร้อมรอ` และปุ่ม `เตรียม Sender`
- Auto warm ตอนเปิด Local UI และ maintenance refill ทุก 60 วินาที
- Warm pool ของ Job จะ refill โดย **ไม่นับ Sender ที่ Job นั้นเคยลองแล้ว** เพื่อรองรับงาน 1,000–3,000 ใจต่อเนื่อง ไม่ติดอยู่กับ warm 100 ไอดีเดิม
- Background warm failure ไม่ disable Sender อัตโนมัติ; Job runtime เป็นผู้ตัดสิน `NEEDS_ATTENTION`

### Connection reuse

- Exact-template DevPlay login ใช้ long-lived thread-local `requests.Session`
- `initMember3` และ DS v4 Heart endpoints ใช้ shared thread-safe urllib3 connection pool
- Friend gRPC ใช้ shared process-wide secure channel + keepalive
- ลดการเปิด TCP/TLS/gRPC channel ใหม่ซ้ำใน ADD / ACCEPT / REMOVE / DS calls

### Batch DB path

- Lease Sender หลายไอดีใน transaction เดียว
- Create `heart_rounds` หลายรายการบน connection เดียว
- Reuse schema ของ P5.1 เท่านั้น
- ไม่มี table/column/migration ใหม่ใน Phase 5.4

### Fast runner

- Warm session ถูกใช้ก่อน cold login เสมอ
- Cold login ยังเป็น fallback ถ้า Warm Pool ยังไม่ทัน
- Stage executor อยู่ยาวตลอด Job แทนการสร้าง ThreadPoolExecutor ใหม่ทุก stage
- ACCEPT fast path ใช้ shared gRPC channel และ adaptive concurrency `10 -> 3 -> 1`
- SEND ยังคง duplicate-safe recovery เดิม: unknown outcome ห้าม blind retry และตรวจ mailbox ก่อน retry explicit failure
- เพิ่ม stage timing ต่อ Batch:
  - prepare/login
  - ADD
  - ACCEPT
  - SEND
  - MAILBOX
  - RECEIVE
  - cleanup

ตัวอย่าง log:

```text
PERF Batch #1: เตรียม 0.12s | ADD 2.10s | ACCEPT 2.80s | SEND 4.20s | MAIL 0.90s | RECEIVE 0.70s
```

## UI

หน้า Local UI เดิมยังใช้ต่อ แต่เพิ่ม:

- การ์ด `พร้อมรอ`
- ปุ่ม `เตรียม Sender`
- ตั้งค่า `Warm Pool เป้าหมาย`
- ตั้งค่า `Warm Login Workers`
- `เตรียมอัตโนมัติเมื่อเปิดโปรแกรม`
- Performance profile ปรับ Warm Pool ด้วย:
  - เสถียร: warm 50 / workers 10
  - สมดุล: warm 100 / workers 20
  - เร็ว: warm 200 / workers 30
  - สูงสุด: warm 300 / workers 50

## วิธีใช้ Local

1. ลง patch แล้วตรวจ version

```powershell
.\run.bat version
```

ต้องเป็น:

```text
3.0.0-phase5.4-performance-engine
```

2. เปิด UI

```powershell
.\run_ui.bat
```

3. หลัง import Sender แล้ว กด `เตรียม Sender` หรือเปิด Auto Warm

4. รอการ์ด `พร้อมรอ` ถึงเป้าหมายก่อน benchmark เพื่อวัด Fast Path จริงที่ไม่มี Sender login อยู่ในเวลาของ Job

5. เริ่มจาก Batch 50 และดู `PERF Batch` ว่าคอขวดอยู่ stage ไหน ก่อนเพิ่ม concurrency

## Test gate ที่ทำใน build

- Python compileall: PASS
- Import/version: PASS
- Synthetic full FastBatch 5/5 using warmed senders: PASS
- Synthetic Warm Pool replacement เมื่อมี login fail: PASS
- Synthetic Warm Pool job-exclusion/refill: PASS
- ไม่มี DB schema migration ใหม่

## Security / secrets

ยังคงกฎเดิม:

- raw password/token/sessionKey/cookie/private template ไม่ออก log
- Exact Template `.private.json` อยู่ local เท่านั้น
- Warm sessions อยู่ใน process memory
- DB เก็บ credential แบบ encrypted vault ตาม P5.1

