# MWOIF Heart — docs

เริ่มที่ไฟล์ตามลำดับนี้เมื่อเกมอัปเดต:

1. `GAME_UPDATE_GUIDE.md` — ขั้นตอนตั้งแต่เปิด binary ใหม่จนถึง live validation
2. `GHIDRA_SEARCH_ANCHORS.md` — คำค้น/Xref ที่ต้องใช้แทนการจำ offset เก่า
3. `CURRENT_BUILD_MAP.json` — map ของ build ปัจจุบันที่ UI แสดงในแท็บ Game Update Map
4. `LIVE_VALIDATION_CHECKLIST.md` — ลำดับทดสอบแบบไม่เสีย cooldown/ไม่ทำ write เกินจำเป็น
5. `ARCHITECTURE.md` — flow และไฟล์ Python ที่รับผิดชอบแต่ละส่วน

หลักสำคัญ: **String/descriptor = anchor, GH address = cache ของ build ปัจจุบันเท่านั้น**

- `UI_V1_GUIDE.md` — การใช้งาน Heart Worker V1 แบบ UI-only

- `V1_UI_GUIDE_TH.md` — คู่มือ UI ภาษาไทยและการเพิ่มไอดีแบบ Copy/Paste
