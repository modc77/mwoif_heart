# Architecture

```text
UI / CLI
  |
  +-- accounts.py       Session/Auth slots 1..100++
  +-- workflow.py       full cycle orchestration
  |     +-- friend_grpc.py
  |     +-- heart_ds.py
  |     +-- heart_mailbox.py
  |     +-- heart_receive.py
  |
  +-- friend_proto.py   exact protobuf wire builders
  +-- grpc_metadata.py  game gRPC metadata
  +-- ds_v4.py          FastLZ + ChaCha20 v4 + Base64
  +-- session_store.py  local Session cache
  +-- auth_store.py     local Auth cache
```

## Runtime model

Receiver เช่น `A` มี Session/Auth 1 ชุดและอยู่คงที่

Sender แต่ละ slot (`1..100++`) มี Session/Auth ของตัวเอง

หนึ่ง cycle:

```text
Sender ADD A
A ACCEPT Sender
Sender SEND A
A MAILBOX filter from Sender
A RECEIVE seq
Sender REMOVE A
```

Batch เป็น sequential ใน baseline เพื่อความเสถียร. การเร่ง/parallel เป็น optimization layer แยก ไม่ควรแก้ protocol layer.
