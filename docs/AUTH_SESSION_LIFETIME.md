# Session / Auth อายุการใช้งาน — V1.2

## สิ่งที่ UI ตรวจได้

หน้า **จัดการไอดี** มี `ทดสอบทั้งหมด` และ `ทดสอบที่เลือก`
โดยใช้เฉพาะ READ ONLY:

1. **Auth** — เรียก `FriendAPI/ListFriends`
2. **Session** — เรียก `game/myMailList.ds`

ไม่มี Add / Accept / Send / Receive / Remove ระหว่างการทดสอบ

## Auth: game_access_token

`game_access_token` เป็น JWT และ payload มี claim `exp`
ดังนั้น UI อ่านเวลา expiry ได้จากตัว token โดยไม่ต้องส่ง token ไปที่อื่น

สำคัญ:

- `imported_at` = เวลาที่ Python นำค่าเข้ามาเก็บ **ไม่ใช่** เวลา expiry
- การแก้ `imported_at`, copy ไฟล์ หรือ save JSON ใหม่ **ไม่ต่ออายุ token**
- ค่า `exp` อยู่ใน token ที่ server ลงนามไว้
- เมื่อเลย `exp` ต้องได้ `game_access_token` ใหม่จากเส้นทาง Login / Refresh / Game materializer

UI จะแสดง `Auth เหลือ` และเตือนเมื่อเหลือน้อยกว่า 30 นาที

## Session: session_key

Session JSON ที่เรามีไม่มี `exp` ให้ดูตรง ๆ
จึงไม่ควรเดาว่าเหลือกี่นาที

วิธีที่เชื่อถือได้ใน V1 คือทดสอบ `myMailList.ds` จริงแบบ READ ONLY:

- ผ่าน = session_key ใช้งานได้ ณ เวลาที่ทดสอบ
- ไม่ผ่าน ขณะที่ Auth ผ่าน = Session/DS context ต้องนำเข้าใหม่

## ทำให้ Session/Auth ไม่หมดได้ไหม

### Auth JWT

ทำให้ `exp` ไม่หมดจากฝั่ง Python ไม่ได้
ต้อง **ออก token ใหม่** เท่านั้น

### session_key

ยังไม่ควรสรุปว่า keep-alive จะต่ออายุได้
เพราะต้องพิสูจน์ก่อนว่า server ใช้ sliding idle timeout หรือ absolute timeout

การยิง request เป็นระยะอาจช่วยได้เฉพาะกรณี sliding session
แต่ถ้า server กำหนด absolute expiry ก็ไม่ช่วย
ดังนั้น V1.2 ยังไม่เปิด keep-alive อัตโนมัติ เพื่อไม่สร้าง request จำนวนมากโดยไม่จำเป็น

## ถ้า Login ยังทำไม่ได้

Fallback ที่ใช้งานง่ายที่สุดตอนนี้:

1. กด `ทดสอบทั้งหมด`
2. ดูไอดีที่ Auth/Session มีปัญหา
3. ดับเบิลคลิกไอดี หรือกด `แก้ไข Session / Auth`
4. ถ้า Auth หมด → วางเฉพาะ Auth ใหม่
5. ถ้า Session หมด → วางเฉพาะ Session ใหม่
6. ช่องที่ไม่วางจะเก็บค่าเดิม
7. กดทดสอบซ้ำ

เมื่อทำ Email/Password Login สำเร็จ ขั้นนี้จะถูกแทนด้วยการสร้าง Session/Auth ใหม่อัตโนมัติ

## Cache vs LAB Auth JSON

Python cache (`mwoif-auth-cache-v1`) เพิ่ม field เพื่อให้ใช้งานสะดวก เช่น:

- `slot`
- `fgs_id`
- `device_id`
- `imported_at`

ค่าพวกนี้ไม่ได้ทำให้ Auth อายุยาวขึ้น

Raw LAB (`mwoif-auth-v13.1`) เป็นแหล่งข้อมูลต้นทาง
ตอน import Python จะดึง `metadata.fgs-id` และ `metadata.device-id`
มาเก็บ top-level ใน cache ให้เอง

`friend-grpc-target` บาง capture อาจไม่มี
V1.2 จึงมี current-build fallback ใน `config.json`:

`gserver.live.prod.devsnova.cloud:443`

เมื่อเกมอัปเดต ให้ตรวจ endpoint นี้ตาม docs เกมอัปเดตด้วย
