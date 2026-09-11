# M WOIF Heart V3 Phase 5.2 - Local Production UI

Patch-only UI layer for the Phase 5.1 production local core.

## Scope

- Adds a desktop control center for local operation.
- Does not add, remove, or alter database tables.
- Does not touch V1/V2 backups.
- Uses the existing Phase 5.1 core runner, sender pool, random selection, lease, cooldown, and direct HTTP exact-template login.
- Secrets are accepted through password fields and are not printed to the UI log.

## Launch

```powershell
.\run_ui.bat
```

## Main Pages

- Dashboard: production status, accounts, jobs, latest events.
- Heart Job: create receiver job and run it through the production runner.
- Sender Pool: set shared sender password, import sender patterns, add one sender, view sender status.
- Jobs: list jobs and run an existing job.
- Activity: UI runtime log and recent DB events.
- Settings: shows runtime defaults and safe reminders.

## Demo Sender Import

For test accounts like `demo0001@gmail.com` through `demo0020@gmail.com`:

- Prefix: `demo`
- Start: `1`
- End: `20`
- Width: `4`
- Domain: `gmail.com`
- Label prefix: `Demo`

Use shared sender password first, then import the pattern.

## Notes

- Phase 5.2 UI is intentionally built on the existing sequential Phase 5.1 runner first.
- 10 sender workers / receiver actor pipeline should be added after the UI can control jobs and show logs cleanly.
- Template files remain private local files under `state/` and must not be shared.
