# Game Update Guide — MWOIF Heart

Baseline ที่ผ่าน Live ตอนนี้: **26.8.02 / build 651**

เป้าหมายของเอกสารนี้คือเวลาเกมอัปเดต เราไม่เริ่มหาใหม่จากศูนย์ ให้ใช้ string/descriptor เดิมเป็น anchor แล้ว map address/function ใหม่เท่านั้น

## 0. กฎก่อนเริ่ม

- เก็บ build เก่าที่ผ่าน Live ไว้เปรียบเทียบ
- สร้าง Ghidra project/program ใหม่สำหรับ binary build ใหม่ อย่าทับ project เก่า
- **ห้าม copy GH address เก่าไปใช้ตรง ๆ** จนกว่าจะยืนยัน Xref ใน build ใหม่
- endpoint/path และ protobuf field number อาจคงเดิม แต่ต้อง verify จาก serializer ทุกครั้ง
- อย่าทดสอบ write ก่อน `friend-list` และ `myMailList.ds` read-only ผ่าน

## 1. อัปเดต Version/Metadata ก่อน

เช็กจากเกมใหม่แล้วแก้ `config.json`:

```json
"game": {
  "version": "NEW_VERSION",
  "build_version": "NEW_BUILD"
}
```

Auth export ใหม่ต้องมี runtime metadata อย่างน้อย:

```text
version
version-code
timezone
country
os.type
os_version
market_type
locale
device-id
device-name
device-model
friend-grpc-target
fgs-id
game-process-elapsed-ms
time-zone-distance
```

## 2. Friend gRPC — หาใหม่ตามลำดับ

### 2.1 Stub / method paths

Search Strings:

```text
/service.api.FriendAPI/ListFriends
/service.api.FriendAPI/SendFriendRequest
/service.api.FriendAPI/HandleFriendRequest
/service.api.FriendAPI/RemoveFriend
```

Baseline 26.8.02 route table อยู่ที่ `FUN_016EE43C`.

สิ่งที่ต้อง record ใน build ใหม่:

```text
ListFriends method path
SendFriendRequest method path
HandleFriendRequest method path
RemoveFriend method path
function ที่ประกอบ FriendAPI stub
```

### 2.2 Add friend protobuf

Search:

```text
service.api.SendFriendRequestRequest.player_id
```

Baseline serializer: `FUN_016F3160`

Baseline wire:

```text
field #2 = player_id string
field #3 = source/type uint
```

ตรวจ instruction ที่เขียน protobuf tag จริง อย่าเดาจากชื่อ field อย่างเดียว

### 2.3 Accept friend protobuf

Search:

```text
service.api.HandleFriendRequestRequest.player_id
```

Baseline serializer: `FUN_016F39D0`

Baseline wire:

```text
field #2 = player_id string
field #3 = accepted bool
```

### 2.4 Remove friend protobuf

Search:

```text
service.api.RemoveFriendRequest.player_ids
```

Baseline wire ที่ Live ผ่าน:

```text
field #2 = player_ids repeated string
```

**จุดที่เคยพลาด:** field #1 ทำให้ server ตอบ `UNKNOWN / Application error processing RPC`. ดังนั้นทุก build ให้เทียบ layout/serializer กับ Add/Accept ก่อน freeze field number.

## 3. Friend gRPC host + metadata

Search metadata strings:

```text
authorization
player-id
index-file-hash
version
version-code
timezone
country
os.type
os_version
login_platform
market_type
fgs-id
locale
device-id
device-name
device-model
```

Baseline metadata builder: `FUN_00EB97FC`

Baseline route:

```text
DevPlay game_endpoints["grpc"]
 -> FUN_00EB7CE4
 -> DAT_0225C988
 -> FUN_00EB8378
 -> common game gRPC channel
```

Python ไม่ควร hardcode host ถ้า Auth metadata มี `friend-grpc-target`; ให้ใช้ metadata เป็นอันดับแรก

## 4. Heart SEND

Search:

```text
game/sendLifeMail2.ds
```

Baseline:

```text
endpoint string GH = 0x007B24A9
builder            = FUN_013D4D10
UI caller          = FUN_01464104
```

Freeze request fieldsใหม่จาก builder:

```text
fromMemberSeq
toMemberSeq
requestId
```

Normal UI baseline ใช้ `requestId = ""`.

## 5. Mailbox / lifeMailBoxSeq

Search:

```text
game/myMailList.ds
mailList
rewardMailList
fromMemberSeq
seq
```

Baseline:

```text
endpoint GH       = 0x007BC9F2
request builder   = FUN_01624E58
response dispatch = FUN_013D0CA4
mailList parser   = FUN_013D3530
reward parser     = FUN_013D36C8
mail item parser  = FUN_013BDB34
```

`FUN_013BDB34` baseline อ่าน:

```text
insertDt
fromMemberSeq
seq
```

`seq` คือค่าเอาไปใช้เป็น `lifeMailBoxSeq`.

## 6. Heart RECEIVE

Search:

```text
game/acceptLifeMail4.ds
lifeMailBoxSeqList
```

Baseline:

```text
endpoint GH = 0x0073B69B
builder     = FUN_013D518C
caller      = FUN_01422AC0
```

Baseline request:

```text
memberSeq
lifeMailBoxSeqList
```

## 7. DS v4 crypto/compression

อย่าเริ่มจาก key hex. เริ่มจาก common DS serializer แล้ว trace ใหม่

Baseline pipeline:

```text
JSON/ValueMap serialization
 -> append random spaces 0..15
 -> FastLZ
 -> decimalOriginalLength + "@" + compressed bytes
 -> ChaCha20 protocol v4
 -> URL-safe Base64
 -> isEncryptedData=4&data=...
```

Baseline functions/addresses:

```text
common encode pipeline = FUN_00F1B108
ChaCha20 wrapper       = FUN_00F3DA48
ChaCha20 core          = FUN_016AF2D4
FastLZ compress        = FUN_016AAB7C
FastLZ decompress      = FUN_016AAB8C
key GH                 = 0x007D2310
base nonce GH          = 0x007D2330
```

Nonce derivation baseline:

```text
nonce[i] = (baseNonce[i] + (preCipherLength & 0xFF)) & 0xFF
counter = 0
```

Server `responseData` อาจตัด `=` Base64 padding; Python ต้องเติม padding ก่อน decode — ตัวนี้แก้ไว้แล้วใน `mwoif/ds_v4.py`.

## 8. Runtime fields ของ DS

Search:

```text
getFgsId
timeZoneDistance
loginPlatform
fgsId
cable=
```

Baseline:

```text
getFgsId string GH = 0x007B31DF
getter              = FUN_0179B4EC
timeZoneDistance GH = 0x00726040
common DS builder   = FUN_011343B8
```

ค่าที่ Python ต้องมีตอน Live:

```text
sessionKey
currentLv
fgsId
ms
timeZoneDistance
accessToken
loginPlatform
cable
```

ถ้า `missing_live_fields` ไม่ว่าง **ห้ามยิง write**.

## 9. อัปเดต map หลังหาเสร็จ

แก้ `docs/CURRENT_BUILD_MAP.json`:

- game.version / build_version
- function/address ใหม่
- status เป็น `STATIC_REVALIDATED`

จากนั้นทดสอบตาม `LIVE_VALIDATION_CHECKLIST.md`. เมื่อ Live ผ่านครบค่อยเปลี่ยน status เป็น `LIVE_VALIDATED`.

## 10. จุดที่ Python มักไม่ต้องแก้

ถ้าสิ่งเหล่านี้ไม่เปลี่ยน Python logic ไม่ต้องแตะ:

- Friend protobuf field numbers
- endpoint paths
- DS v4 algorithm
- mailbox response keys
- runtime metadata key names

ส่วนที่มักต้องอัปเดตคือ **game version/build, LAB extractor/native GH offsets และ static crypto addresses/material ถ้าเกมย้ายหรือเปลี่ยน**.
