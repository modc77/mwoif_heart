# V3 Phase 5.4.2 — Turbo 100 / Wave Pipeline

เป้าหมายของ patch นี้คือเจาะคอขวดจาก benchmark P5.4.1 โดยไม่เปลี่ยน DB schema และไม่แก้ Login/Heart protocol ที่ผ่านแล้ว

## สิ่งที่เปลี่ยน

- เพิ่ม UI profile `เทอร์โบ 100`
  - Friend slots: 100
  - Batch: 100
  - Sender workers: 50
  - Network workers: 50
  - Warm Pool target: 150
  - Warm login workers: 50
- Fast runner รองรับ Batch สูงสุด 100 (เดิม 50)
- Batch >= 75 จะเข้า Turbo pipeline อัตโนมัติ
- หลัง ADD จะรอ propagation สั้น ๆ แล้วทำ `ACCEPT -> SEND` แบบ pipeline ต่อ Sender
  - Sender ที่ ACCEPT ผ่าน ไม่ต้องรอ Sender อื่นครบก่อนเริ่ม SEND
  - ACCEPT และ SEND จึง overlap กัน
- ACCEPT มี bounded retry + deterministic jitter แทน global `10 -> 3 -> 1` wall ทั้ง batch
- SEND ยังคง duplicate-safe
  - response ไม่แน่ชัด => ตรวจ Mailbox ก่อน
  - unknown outcome ห้าม blind retry
  - explicit fail ที่ยืนยันว่าไม่มี mail เท่านั้นจึง retry
- Stable path เดิมยังอยู่สำหรับ Batch <= 50
- เพิ่ม log `PERF TURBO` เพื่อดูเวลาของ pipeline ที่ซ้อน ACCEPT/SEND

## ไม่มีการเปลี่ยนแปลง

- ไม่มี DB migration
- Pair cooldown / lease / random / replacement เหมือนเดิม
- Warm Pool / Direct HTTP Exact Template เหมือนเดิม
- Batch mailbox + multi-seq receive เหมือนเดิม
- Friend preflight + confirm cleanup เหมือนเดิม

## วิธี benchmark

1. Warm Sender ให้มีไอดีพร้อมมากกว่า 100 และต้องมี eligible สำหรับ Receiver อย่างน้อย 100
2. Settings -> `เทอร์โบ 100` -> บันทึกค่า
3. สร้าง Job 100 ใจ
4. ดู log:

```
TURBO: ... ACCEPT→SEND แบบซ้อนกัน
TURBO ACCEPT: ผ่าน 100/100 • SEND ยืนยัน 100/100 • pipeline ...s
PERF TURBO Batch #1: เตรียม ... | ADD ... | ACCEPT→SEND OVERLAP ... | MAIL ... | RECEIVE ...
```

เป้าหมายเป็น benchmark ไม่ใช่การรับประกันเวลา: วัด server จริงแล้วค่อยจูน accept/send workers และ propagation delays ต่อไป
