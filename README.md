# MWOIF Heart — Clean Multi-Account

สถานะนี้เป็นฐาน **ที่ผ่าน live แล้ว** สำหรับ flow:

```text
Sender -> Add Receiver
Receiver -> Accept Sender
Sender -> Send Heart
Receiver -> Read Mailbox / find seq
Receiver -> Receive Heart
Sender -> Remove Receiver
```

## โครงสร้างบัญชี

- Receiver หลักตั้งเป็น `A`
- Sender ใช้ slot ได้ไม่จำกัด เช่น `1`, `2`, `3`, `S001`, `S002` ...
- ไม่ผูกระบบไว้กับ A/B อีกแล้ว
- ทุก slot ต้องมี **Session + Auth** ของตัวเอง

ตัวอย่าง 100 sender:

```text
A      = Receiver
1..100 = Senders
```

แต่ละ sender ทำ cycle ของตัวเองกับ A:

```text
1 -> A: add -> accept -> send -> receive -> remove
2 -> A: add -> accept -> send -> receive -> remove
...
```

ระบบ batch ทำแบบ **sequential** ก่อนเพื่อให้ baseline เสถียร
เรื่องเร่งความเร็ว/parallel ค่อยทำหลังจากนี้

## ติดตั้ง

ดับเบิลคลิก:

```text
run.bat
```

ครั้งแรกจะสร้าง `.venv` และติดตั้ง dependencies ให้อัตโนมัติ

หรือ:

```powershell
powershell -ExecutionPolicy Bypass -File setup_windows.ps1
```

## Import account

### ทีละ account

```powershell
python main.py account-import A --session path\session_A.json --auth path\auth_A.json
python main.py account-import 1 --session path\session_1.json --auth path\auth_1.json
python main.py account-import 2 --session path\session_2.json --auth path\auth_2.json
```

### Import หลาย account เป็น directory

วางไฟล์แบบนี้:

```text
imports/
  A/
    session.json
    auth.json
  1/
    session.json
    auth.json
  2/
    session.json
    auth.json
```

แล้ว:

```powershell
python main.py import-dir imports
```

`imports/` และ `.state/` ถูก `.gitignore` ไว้แล้ว

## ดูบัญชี

```powershell
python main.py accounts
python main.py doctor
```

## Preview 1 cycle

```powershell
python main.py cycle 1 --receiver A
```

## Run 1 sender จริง

```powershell
python main.py cycle 1 --receiver A --live
```

## Run หลาย sender

```powershell
python main.py batch 1,2,3 --receiver A --live
python main.py batch 1-20 --receiver A --live
python main.py batch 1,3,5-10 --receiver A --live
```

ทุก account ที่ ready ยกเว้น A:

```powershell
python main.py batch --receiver A --all --live
```

## เมนู

```powershell
python main.py menu
```

หรือดับเบิลคลิก `run.bat`

## Git safety

ไฟล์ต่อไปนี้ **ห้าม commit** และถูก ignore แล้ว:

```text
.state/*.json
imports/**
import/**
account.md
.env*
secrets.local.json
```

ZIP clean นี้ไม่มี Session/Auth จริงของบัญชีติดมาด้วย

## ขั้นถัดไป

Email/password login **ยังไม่ได้รวมใน clean baseline นี้**
เพราะขั้นนั้นจะทำแยกหลังจาก commit ฐานที่ live ผ่านทั้งหมดแล้ว
