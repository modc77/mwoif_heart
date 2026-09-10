# MWOIF HEART V2 — AUTH / LOGIN / SESSION HANDOFF

วันที่สรุป: 2026-09-10  
สถานะตอนสรุป: 5.6

> เอกสารนี้สรุปข้อมูลที่ทำ/พิสูจน์ในรอบนี้ เพื่อเอาไปต่อแชทใหม่หรือใช้เป็น handoff งาน V2  
> ไม่มีการบันทึก email/password, raw token, raw cookie, raw URL secret, sessionKey จริง หรือค่า secret ใด ๆ

---

## 1. เป้าหมายหลัก

ต้องการทำ `mwoif_heart V2` โดยไม่แก้หรือทำให้ `V1` พัง

แนวคิดหลัก:

```text
V1 = logic ส่ง/รับใจที่พิสูจน์แล้ว
V2 = เพิ่มระบบ Login/Auth/Session ใหม่ แล้วต่อเข้ากับ flow V1

V1 มีครบแล้ว:

Sender Add Receiver
→ Receiver Accept Sender
→ Sender Send Heart
→ Receiver Read Mailbox
→ Receiver Receive Heart
→ Sender Remove Receiver

สิ่งที่ V1 ยังขาดสำหรับระบบใหม่คือ:

email/password
→ login เอง
→ ได้ LoginSession/Auth
→ initMember3
→ ได้ memberSeq/currentLv/sessionKey
→ เอาไปใช้กับ flow V1
2. กฎและ scope ที่ยึด
ห้ามแก้ V1 เดิม
V2 ต้องแยกจาก V1
ห้ามสร้างโปรเจกต์ใหม่ทั้งก้อนถ้าเป็น patch
แก้เฉพาะส่วนที่เกี่ยวกับ V2 login/auth/session
ห้ามแตะ Giant/Super/Powder/Tower/feature อื่น
ห้ามใส่ค่าเดา/ปลอม
ห้าม hardcode fingerprint แทน secret จริง
ห้าม output raw password/token/sessionKey/cookie
Session TTL / ValidateSession deep research พักไว้ก่อน เพราะถ้า login ใหม่ทุกครั้งได้ session ใหม่ก็ไม่ใช่ blocker ตอนนี้
3. สถานะ V1 ที่ใช้เป็นฐาน

V1 mwoif_heart ทำงานได้แล้วในส่วน protocol/workflow

Friend gRPC

Host:

gserver.live.prod.devsnova.cloud:443

Methods:

/service.api.FriendAPI/ListFriends
/service.api.FriendAPI/SendFriendRequest
/service.api.FriendAPI/HandleFriendRequest
/service.api.FriendAPI/RemoveFriend

protobuf สำคัญ:

SendFriendRequestRequest:
- field #2 = player_id
- field #3 = source/type = 2

HandleFriendRequestRequest:
- field #2 = player_id
- field #3 = accept = true

RemoveFriendRequest:
- field #2 = player_ids repeated string

ข้อควรระวัง:

RemoveFriend ต้องใช้ field #2 เท่านั้น
ห้าม regress กลับไปใช้ field #1 เพราะเคยทำให้ server error
Heart DS

Endpoints:

game/sendLifeMail2.ds
game/myMailList.ds
game/acceptLifeMail4.ds

Send heart:

game/sendLifeMail2.ds
fields:
- fromMemberSeq
- toMemberSeq
- requestId = ""

Mailbox:

game/myMailList.ds
ใช้:
- mailList[].seq
- mailList[].fromMemberSeq
- mailList[].insertDt

mailList[].seq = lifeMailBoxSeq

Receive heart:

game/acceptLifeMail4.ds
request:
- memberSeq
- lifeMailBoxSeqList
DS v4

Encoding ที่ V1 ใช้และห้ามแก้มั่ว:

JSON
→ random padding spaces 0..15
→ FastLZ
→ ascii(uncompressed_length) + "@" + compressed
→ ChaCha20 v4
→ URL-safe Base64
→ form-urlencoded isEncryptedData=4&data=...

Response reverse:

URL-safe Base64
→ ChaCha20 v4 decrypt
→ decimal length + "@"
→ FastLZ decompress
→ JSON
4. DEX/JAVA static ที่ยืนยันแล้ว

โฟกัส class:

com/devplay/newauth/manager/AuthManager

จุดสำคัญที่เจอ:

AuthManager.getNowLoggedInSession()

คืนค่าเป็น String ไม่ใช่ LoginSession object ดังนั้นไม่ควรใช้เป็นทางหลักในการอ่าน token fields

ทางที่ถูกต้อง:

AuthManager.INSTANCE
→ getLoginSession()
→ LoginSession

Refresh route ที่ถูกต้อง:

AuthManager.INSTANCE
→ getAuthLifecycleService()
→ AuthLifecycleService.updateAccessToken()

Login callback ที่จับได้จริง:

AuthManager.handleLoginResult$handleSuccess
5. LoginSession fields ที่ต้องใช้

LoginSession มี field หลัก:

refreshToken
gameAccessToken
ovenAccessToken
expiredDate
member
linkStatus

จาก runtime LAB ยืนยันว่า login สำเร็จแล้ว fields เหล่านี้มีจริง:

refreshToken present
gameAccessToken present
ovenAccessToken present
expiredDate present

LAB ต้องไม่ output raw values ของ fields เหล่านี้

6. LAB V2 runtime ที่ทำและผลลัพธ์

สร้าง LAB สำหรับทดสอบ:

M WOIF LAB • AUTH / SESSION V2

ปุ่ม/flow ที่ทำ:

0. DUMP LOGIN WEB CONTEXT
1. TEST LOGIN FLOW
2. TEST AUTH REFRESH
3. TEST SESSION
COPY V2 RESULT
6.1 TEST LOGIN FLOW — ผ่านแล้ว

ผล runtime:

RESULT=LOGINSESSION_RUNTIME_CONFIRMED

Hook ที่ทำงาน:

AuthManager: HOOKED
CocosLoginDelegate: HOOKED
CocosAuthDelegate: HOOKED

callback ที่เจอ:

AuthManager.handleLoginResult$handleSuccess

ผลที่ยืนยัน:

callback observed = true
LoginSession changed = true
refreshToken present = true
gameAccessToken present = true
ovenAccessToken present = true
expiredDate present = true
passwordCaptured = false
secretOutput = NONE

สรุป:

LOGIN FLOW = PASS
6.2 TEST AUTH REFRESH — ผ่านแล้ว

ผล runtime:

RESULT=REFRESH_RUNTIME_CONFIRMED

route:

AuthManager.getAuthLifecycleService
→ AuthLifecycleService.updateAccessToken

source:

LOGIN_SESSION + GAME_MATERIALIZER

ผลก่อน/หลัง refresh:

refreshToken = เดิม
gameAccessToken = เปลี่ยน
ovenAccessToken = เปลี่ยน
expiredDate = เปลี่ยน
sessionExpired = false
secretOutput = NONE

สรุป chain ที่พิสูจน์แล้ว:

LoginSession.refreshToken
→ AuthManager.getAuthLifecycleService()
→ AuthLifecycleService.updateAccessToken()
→ LoginSession updated
   ├─ refreshToken เดิม
   ├─ gameAccessToken ใหม่
   ├─ ovenAccessToken ใหม่
   └─ expiredDate ใหม่

ข้อสังเกต:

callbackHook=METHOD_NOT_FOUND
callback.nativeOnTokenRefreshed=false

แต่ไม่ถือว่า refresh fail เพราะ state ก่อน/หลังยืนยันชัดว่า token และ expiry เปลี่ยนจริง

สรุป:

AUTH REFRESH = PASS
6.3 TEST SESSION — partial / พักไว้ก่อน

ผล runtime:

RESULT=SESSION_KEY_STABLE
reason=VALIDATE_NATIVE_RPC_ENTRY_NOT_WIRED_YET
static.classification=VALIDATE_ONLY
validateRpc=NOT_WIRED

ข้อมูลที่เห็น:

before.memberSeq = present
before.currentLv = present
before.session.fp = present
after.memberSeq = same
after.currentLv = same
after.session.fp = same
changed.session = false
same.member = true
secretOutput = NONE

Static schema ที่รู้:

ValidateSessionRequest.field2 = session_key
ValidateSessionResponse.field2 = player_id

สรุป:

SESSION deep validation ยังไม่ครบ
ValidateSession RPC ยังไม่ได้ wire จริง
แต่ตอนนี้ไม่ใช่ blocker เพราะแผน V2 จะ login ใหม่เพื่อสร้าง session ใหม่
7. เหตุผลที่พัก SESSION TTL / ValidateSession ไว้ก่อน

แนวคิด V2 ที่ตกลงกัน:

ทุกครั้งที่จะทำงาน:
1. Login receiver ด้วย email/password
2. initMember3 เพื่อสร้าง session ใหม่
3. Login sender แต่ละตัวด้วย email/password
4. initMember3 เพื่อสร้าง session ใหม่
5. ใช้ session รอบนั้นทำ Friend/Heart flow

ดังนั้นสิ่งที่จำเป็นคือ:

login ได้
→ initMember3 ได้
→ ได้ sessionKey ใหม่
→ ทำงานต่อได้

สิ่งที่ยังไม่จำเป็นตอนนี้:

sessionKey หมดอายุเมื่อไร
ValidateSession renew/reissue หรือไม่

Recovery plan ถ้า session invalid:

Receiver session invalid
→ login receiver ใหม่
→ initMember3 ใหม่
→ ใช้ sessionKey ใหม่
→ ทำงานต่อจาก sender ปัจจุบัน

Sender session invalid
→ login sender ใหม่
→ initMember3 ใหม่
→ retry sender ปัจจุบัน
8. Login WebView context ที่ dump ได้จาก LAB

ปัญหา Python V2 login เดิม:

Python เปิด URL เปล่า:
https://app.devplay.com/auth/v2/login-try

ผลคือหน้า login แสดงได้ แต่พอกด continue แล้วขึ้น:

UNKNOWN (40001)

สาเหตุ:

เกมจริงไม่ได้เปิด /auth/v2/login-try เปล่า
เกมจริงเปิดพร้อม query parameters + Cookie header

LAB dump ยืนยัน:

RESULT=LOGIN_WEB_CONTEXT_CAPTURED
route=AuthManager.getAuthWebViewModel->AuthWebViewModel.getUrl(Login)+getCustomHeader
url.scheme=https
url.host=app.devplay.com
url.path=/auth/v2/login-try
url.query.count=36
header.count=1
header.names=Cookie
cookie.names=api_key,bundle_id,lang,platform,sdk,sdk_version
rawUrlOutput=NONE
rawCookieOutput=NONE
secretOutput=NONE
8.1 Query names ทั้ง 36 ตัว
agree_ad_day_push
agree_ad_night_push
agree_dt
country_code
device_id
device_type
email_address
fallback_country_code
lang
lc.anonymous_id
lc.app_build
lc.app_installed_id
lc.app_version
lc.device.manufacturer
lc.device.model
lc.device.traits
lc.device.version
lc.devsisters_id
lc.fgs_id
lc.library_name
lc.library_version
lc.locale_on_game
lc.location_country
lc.os_name
lc.os_version
lc.platform
lc.semi_device_id
lc.store
lc.timezone
push_token
recall_session_id
terms_updates_ids
terms_updates_values
timezone
use_popup_v2
use_terms_v2
8.2 Cookie names
api_key
bundle_id
lang
platform
sdk
sdk_version
8.3 ค่า query ที่ present=false ตอน dump
email_address
lc.device.traits
lc.platform
recall_session_id

แปลว่า key มีใน URL schema แต่ค่าบางตัวอาจว่างตามสถานะ runtime

9. สิ่งที่ต้องแก้ใน Python V2 ต่อ

ต้อง patch เฉพาะ V2 Python login adapter ไม่สร้างใหม่ทั้งโปรเจกต์

เป้าหมาย patch:

DevPlay login adapter เดิม
→ build https://app.devplay.com/auth/v2/login-try
→ เติม 36 query parameters ตามชื่อที่ LAB ยืนยัน
→ เติม Cookie header 6 key
→ ใช้ค่าจาก config/runtime ที่มีจริง
→ ไม่ hardcode fingerprint
→ ไม่ output raw secrets

ต้องไม่แก้:

V1
Friend gRPC
Heart DS
DS v4 codec
RemoveFriend protobuf field
UI อื่น
LAB feature อื่น
Giant/Super/Powder/Tower
9.1 ค่าที่ Python V2 ควรดึงจาก config/runtime

จาก config เดิมมีข้อมูลประมาณนี้:

game.version = 26.8.02
game.build_version = 651
locale = en-US หรือ en
location_country = US
os_type = A
os_version = 12
timezone = Asia/Bangkok
time_zone_distance = 25200
market_type = GOOGLE_PLAY
login_platform = email
device_name/model/id
fgs_id

Mapping ที่ควรทำอย่างระวัง:

lc.app_version         ← game.version
lc.app_build           ← game.build_version
lc.locale_on_game      ← locale/lang
lc.location_country    ← location_country
lc.os_name             ← Android / device type จาก runtime/config
lc.os_version          ← os_version
lc.timezone            ← timezone
lc.store               ← GOOGLE_PLAY หรือ store value ที่ SDK ใช้
lc.fgs_id              ← fgs_id runtime/config
lc.semi_device_id      ← semi device id runtime/config ถ้ามี
lc.device.model        ← device model
lc.device.manufacturer ← manufacturer
lc.device.version      ← device/version string จาก runtime/config ถ้ามี

ค่าไหนไม่มีหลักฐานจาก config/runtime ให้ปล่อยว่างหรือ derive แบบระบุชัดใน code comment ห้ามใส่ค่าปลอม

9.2 Cookie header ที่ต้องมี
Cookie: api_key=...; bundle_id=...; lang=...; platform=...; sdk=...; sdk_version=...

ต้องหาค่าจาก config/runtime/DEX ที่ยืนยันแล้วเท่านั้น

10. สถานะไฟล์/ZIP ที่สร้างในรอบนี้

LAB ZIP ที่เกิดระหว่างทาง:

MWOIF_LAB_AUTH_SESSION_V2_FULL_RUNTIME_SRC.zip
MWOIF_LAB_AUTH_SESSION_V2_VISIBLE_LOG_FIX_SRC.zip
MWOIF_LAB_AUTH_SESSION_V2_ROUTE_FIX_SRC.zip
MWOIF_LAB_AUTH_SESSION_V2_LOGIN_WEB_CONTEXT_DUMP_SRC.zip
MWOIF_LAB_AUTH_SESSION_V2_LOGIN_WEB_CONTEXT_DUMP_COMPILE_FIX_SRC.zip
MWOIF_LAB_AUTH_SESSION_V2_WEB_CONTEXT_ROUTE_FIX_SRC.zip

V2 Python ZIP ที่สร้างก่อนพบปัญหา login context:

MWOIF_HEART_V2_LOGIN_PLUS_V1_FLOW_FULL_SRC.zip

ปัญหาใน V2 Python ZIP นั้น:

login adapter เปิด /auth/v2/login-try แบบ URL เปล่า
จึงเจอ UNKNOWN (40001)

สถานะหลัง LAB dump:

ต้อง patch V2 Python login adapter จากข้อมูล Web Context ที่ LAB ยืนยัน
11. Error และวิธีแก้ที่เจอระหว่างทาง
11.1 PowerShell ไม่เจอ run_v2.bat

Error:

run_v2.bat : The term 'run_v2.bat' is not recognized

วิธีรันใน PowerShell:

.\run_v2.bat login-test

เพราะ PowerShell ไม่รัน script จาก current directory โดยไม่ระบุ .\

11.2 CMake reply was not a directory

Error:

app\.cxx\Debug\...\.cmake\api\v1\reply was not a directory

สาเหตุ:

CMake generated cache/workdir เสีย
ไม่ใช่ Kotlin LAB logic โดยตรง

แนวแก้:

ลบโฟลเดอร์ app/.cxx
แล้ว build ใหม่
11.3 unknown op

Error:

unknown op=auth_login_v2
unknown op=auth_login_web_context_v2

สาเหตุ:

UI ส่ง op แล้ว แต่ dispatcher/backend ยังไม่รับ op หรือ allowlist ยังไม่มี op
หรือเกมยังโหลด process เก่าอยู่

แนวแก้:

FloatingMenuService + InProcessGameControl + Backend ต้องมาเป็นชุดเดียวกัน
Build APK ใหม่
ติดตั้งใหม่
ปิด Cookie Run ให้หมด process
เปิดเกมใหม่
11.4 Kotlin compile error ใน FloatingMenuService

Error:

Expecting '"'
Unexpected tokens
Unresolved reference: btnLp

สาเหตุ:

มี string literal ขึ้นบรรทัดผิดใน Kotlin
และใช้ btnLp() ที่ไม่มีในไฟล์นั้น

แก้แล้วใน compile fix ZIP

12. Current status ล่าสุด
LAB LOGIN FLOW       = PASS
LAB AUTH REFRESH     = PASS
LAB SESSION          = PARTIAL / deferred
LAB WEB CONTEXT DUMP = PASS
Python V2 login-test = FAIL เพราะ login URL context ยังไม่ครบ

สาเหตุ Python V2 fail:

เปิด /auth/v2/login-try เปล่า
ขาด 36 query parameters
ขาด Cookie header 6 key
ทำให้ DevPlay ตอบ UNKNOWN (40001)

สิ่งที่ต้องทำต่อ:

Patch เฉพาะ V2 Python login adapter
ใช้ Web Context จาก LAB
ไม่สร้าง project ใหม่
ไม่แตะ V1
ไม่ใส่ค่าปลอม
ทดสอบ login-test ใหม่
13. Next prompt สำหรับแชทใหม่
ต่อจาก handoff นี้ ให้แก้เฉพาะ V2 Python login adapter ในไฟล์ v2.zip ที่ฉันอัปโหลด
ห้ามแตะ V1 และห้ามสร้าง project ใหม่ทั้งก้อน
เป้าหมายคือแก้ UNKNOWN (40001) ของ DevPlay login
ใช้ข้อมูล LAB Web Context ที่ยืนยันแล้ว:
- host app.devplay.com
- path /auth/v2/login-try
- query names 36 ตัว
- Cookie header keys: api_key,bundle_id,lang,platform,sdk,sdk_version
ห้าม hardcode fingerprint หรือค่า secret ปลอม
ให้ดึงค่าจาก config/runtime ที่มีจริง ถ้าค่าไหนไม่มีให้ปล่อยว่างหรือระบุว่า missing
แพ็กเป็น patch ZIP กลับมา แล้วบอกไฟล์ที่แก้เท่านั้น
14. Secret handling rule

ห้ามบันทึกหรือทวนกลับ:

email จริง
password จริง
raw refreshToken
raw gameAccessToken
raw ovenAccessToken
raw sessionKey
raw Cookie header
raw login URL ที่มี secret/query เต็ม

ให้ใช้เฉพาะ:

present=true/false
len
fingerprint
exp timestamp
changed=true/false

ตอนนี้ใช้เนื้อหานี้ไปแชทใหม่ได้เลย แล้วอัป `v2.zip` ให้มัน patch ต่อครับ.
```
