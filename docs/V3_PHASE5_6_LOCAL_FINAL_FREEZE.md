# M WOIF Heart V3 — Phase 5.6 Local Final Freeze

## สถานะ

Phase 5.6 ปิดงาน **Local/Lab V1** โดยไม่เปลี่ยน Heart protocol, Fast Batch, DB schema หรือกติกา network ที่ผ่านการทดสอบแล้ว

Release:

```text
M_WOIF_HEART_LOCAL_V1
version=3.0.0-phase5.6-local-v1-final
schema_changed=false
```

## Baseline ที่ล็อก

- Direct HTTP Exact Template login — browser ระหว่าง replay = NO
- initMember3 / Session — PASS
- Friend gRPC — PASS
- Heart DS-v4 / Mailbox — PASS
- Update Guard — PASS 4/4
- Local Sender policy — ไม่ auto-disable / ไม่ quarantine จาก runtime error
- Pair cooldown Sender→Receiver หลังส่งสำเร็จจริง — คงไว้
- Warm Sender Pool — คงไว้
- Adaptive Wave + Accept Catch-up — คงไว้
- Batch mailbox / multi receive — คงไว้
- Local benchmark ล่าสุดที่ operator ยืนยัน: 157/157, error=0; Batch 100 แรกได้ 99 ใจประมาณ 32.2 วินาที

## สิ่งที่เพิ่มใน P5.6

1. `local-final-check` — preflight แบบไม่ทำ game action และไม่เปิด browser
2. `final_check.bat` — เรียก final check ได้ทันทีบน Windows
3. Final manifest + Update runbook
4. Web/Python handoff สำหรับย้าย Core ไป worker ภายหลัง
5. UI เปลี่ยนป้ายจาก Prototype เป็น `LOCAL LAB • FINAL V1`

## กติกา Freeze

หลัง P5.6 ห้ามแก้ Heart Engine ใน Local เพื่อเพิ่มความเร็วโดยไม่มี regression test ใหม่

เมื่อเกมอัปเดต:

1. เปิด Local
2. กด `ตรวจระบบ`
3. ต้อง PASS 4/4
4. ถ้า FAIL ให้แก้เฉพาะ layer ที่ fail
5. รัน Heart benchmark สั้นหลังแก้
6. Web worker ค่อยรับ patch เดียวกันหลัง Local ผ่าน

Local มีหน้าที่เป็น Lab/Reference implementation ไม่ใช่ UI production ของลูกค้า
