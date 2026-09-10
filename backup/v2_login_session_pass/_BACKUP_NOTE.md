# V2 Login/Session PASS Backup

สำเนานี้คือ V2 ที่ผ่าน `login-test` แล้ว:

```text
DevPlay login -> LoginSession -> initMember3 -> DS v4/FastLZ decode -> memberSeq/sessionKey
```

ไม่รวม runtime secrets เช่น `state/login_web_context.private.json`, `receiver.test.local.json`, token/cookie/session files, `.git`, `__pycache__`
