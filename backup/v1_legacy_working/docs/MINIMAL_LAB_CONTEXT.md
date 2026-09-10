# V1.3 — Minimal LAB Context

Python เป็นเจ้าของค่าคงที่/ค่าประจำ build แล้ว LAB ไม่ต้อง copy metadata ยาว ๆ อีก

## Session ที่ LAB ต้องส่ง

```json
{
  "schema": "mwoif-session-min-v1",
  "member_seq": 123456789,
  "current_lv": 1,
  "session_key": "..."
}
```

ค่าที่เปลี่ยนจริง: `member_seq`, `current_lv`, `session_key`

## Auth ที่ LAB ต้องส่ง

```json
{
  "schema": "mwoif-auth-min-v1",
  "mid": "PLAYER001",
  "game_access_token": "...",
  "fgs_id": "...",
  "game_process_elapsed_ms": 123456
}
```

ค่าที่เปลี่ยนจริง: `mid`, `game_access_token`, `fgs_id`, `game_process_elapsed_ms`

## Python เติมให้อัตโนมัติ

- runtime-metadata-version
- player-id = mid
- version / version-code
- timezone / country
- os.type / os_version
- locale
- device-name / device-model / device-id
- index-file-hash
- time-zone-distance
- market_type
- login_platform
- friend-grpc-target
- source / slot / imported_at

`friend-grpc-target` ถูกเติมจาก `config.server.friend_grpc_target` เสมอถ้า LAB ไม่มีค่า

## Backward compatibility

V1.3 ยังรับ JSON เก่า `mwoif-session-v13` และ `mwoif-auth-v13.1` ได้ เพื่อไม่ทำให้ cache/workflow เดิมพัง
