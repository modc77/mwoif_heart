# M WOIF Heart V3 — Phase 5.4.6 Local No-Disable + Retryable Pre-Send

## เป้าหมาย
Local Control Center เป็นเครื่องมือของเจ้าของระบบเอง จึงไม่ auto-disable Sender จาก login/auth/session error ใด ๆ ส่วน policy quarantine สำหรับ Web ให้แยกทำภายหลัง

## ปัญหาที่แก้จาก Job #20
- 150 Sender ถูกลองครบแล้ว แต่สำเร็จ 121
- 121 ตัวที่ส่งสำเร็จติด pair cooldown ตามปกติ
- 29 ตัวที่ ACCEPT ไม่ผ่านถูกบันทึกเป็น attempted ทำให้ selector มองว่าใช้ไปแล้วและไม่หยิบกลับมา
- จึงขึ้น eligible=0 ทั้งที่ 29 ตัวนั้นยังไม่ได้ SEND และควรลองใหม่ได้อย่างปลอดภัย

## พฤติกรรมใหม่
- Local ไม่ตั้ง `enabled=0` จาก runtime/login error
- Local ไม่ย้าย Sender ไป health `error` เพื่อกัน selection; Error ยังเก็บใน last_error/log
- Sender ที่เคยถูก auto-disable ด้วย health error/disabled จะถูกคืนเป็น enabled + unknown เมื่อเริ่ม Job
- ADD/ACCEPT failure เป็น `retryable pre-send` เพราะยังไม่มี heart SEND
- retryable ADD/ACCEPT จะไม่บล็อก Sender ใน `heart_job_sender_attempts` สำหรับ Job เดิม
- Retry lease เดิมจะล้าง metadata attempt เก่าก่อนเริ่มรอบใหม่
- ACCEPT catch-up เพิ่มเป็นสูงสุด 5 wave แบบลด worker ลงเรื่อย ๆ
- ถ้ายังเหลือหลัง catch-up ระบบจะคืน Sender เหล่านั้นเข้าคิว Batch ถัดไป ไม่กิน Sender ใหม่โดยไม่จำเป็น
- SEND unknown / mailbox missing / receive failure ยังใช้ duplicate-safe recovery/cooldown เดิม ห้าม blind retry
- Pair cooldown หลัง RECEIVE สำเร็จยังคง 1 ชั่วโมงตาม policy เดิม เพราะนี่ไม่ใช่การ disable account
- ป้องกัน loop: ถ้า 3 Batch ติดกันไม่ได้ใจเพิ่ม ระบบ pause Job แต่ไม่ปิด Sender ใด ๆ

## ไม่มี Schema Migration
ไม่เพิ่ม/ลบตาราง และไม่แก้ข้อมูลลับ
