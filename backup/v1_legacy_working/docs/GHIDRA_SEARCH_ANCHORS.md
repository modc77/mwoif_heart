# Ghidra Search Anchors

ใช้รายการนี้เวลาอัปเดตเกม แทนการไล่ offset แบบสุ่ม

| กลุ่ม | Search string / descriptor | สิ่งที่ต้องตาม Xref |
|---|---|---|
| Friend route | `/service.api.FriendAPI/ListFriends` | FriendAPI stub / route table |
| Friend Add | `service.api.SendFriendRequestRequest.player_id` | request serializer + protobuf tags |
| Friend Accept | `service.api.HandleFriendRequestRequest.player_id` | request serializer + accepted bool tag |
| Friend Remove | `service.api.RemoveFriendRequest.player_ids` | repeated player_ids serializer/tag |
| Heart Send | `game/sendLifeMail2.ds` | request builder -> common DS send |
| Mailbox | `game/myMailList.ds` | request builder + response parser |
| Mailbox item | `fromMemberSeq`, `seq`, `mailList` | item parser / seq extraction |
| Heart Receive | `game/acceptLifeMail4.ds`, `lifeMailBoxSeqList` | accept builder + caller |
| Runtime | `getFgsId` | JNI/game getter |
| Runtime | `timeZoneDistance` | common DS builder |
| Runtime | `loginPlatform`, `fgsId`, `cable=` | common DS fields |
| gRPC meta | `authorization`, `player-id`, `version-code` | metadata builder |
| DS envelope | `isEncryptedData` | encode/decode pipeline |

## วิธีบันทึกทุก anchor

ทุกตัวให้จด 5 อย่าง:

```text
ANCHOR_STRING=
STRING_GH=
XREF_FUNCTION=
SERIALIZER/BUILDER=
FIELD/TAG PROOF=
```

ตัวอย่าง Remove:

```text
ANCHOR_STRING=service.api.RemoveFriendRequest.player_ids
OLD_RESULT=field #2 repeated string
NEW_RESULT=<ตรวจจาก serializer build ใหม่>
PROOF=<instruction/tag write address>
```

ถ้า anchor string หาย ให้ค้นชื่อ type/class จาก RTTI ก่อน เช่น `RemoveFriendRequest`, แล้วไล่ generated protobuf vtable/serializer แทน
