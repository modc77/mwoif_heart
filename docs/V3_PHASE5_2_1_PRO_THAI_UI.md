# M WOIF Heart V3 — Phase 5.2.1 Professional Thai Local UI

Replacement UI for the local production prototype.

## Scope

- Replaces the previous Tkinter screen completely with PySide6.
- Thai-first compact dark control center.
- No database schema migration.
- Uses the existing Phase 5.1 production Core/DB/Vault/Direct HTTP template login.
- UI business logic stays in `UiController`; production Core remains reusable by a future web worker.

## Main screens

1. **ปั้มใจ** — receiver email/password/amount, system counters, live log, latest job status.
2. **ไอดีส่ง** — Sender Pool, search/filter, add one, bulk pattern import, shared password.
3. **งาน** — job table and resume/run existing job.
4. **ต้องตรวจสอบ** — sender accounts flagged by account/runtime errors.
5. **บันทึก** — structured DB events.
6. **ตั้งค่า** — non-secret UI defaults only.

## Demo bulk pattern

Defaults are intentionally prepared for the current test set:

- prefix: `demo`
- start: `1`
- end: `20`
- width: `4`
- domain: `gmail.com`

This produces `demo0001@gmail.com` through `demo0020@gmail.com`.

For real sender identities later, change the pattern to the production prefix and desired width/range. The UI only imports identities that already exist; it does not register accounts.

## Install / run

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run_ui.bat
```

## Security

- Receiver password is passed to the existing encrypted job vault.
- Shared sender password is stored through the existing AES-GCM vault.
- `state/ui_preferences.json` stores only non-secret UI defaults (template IDs and amount limit).
- Runtime console applies an extra redaction guard; Core secret-output rules remain unchanged.
