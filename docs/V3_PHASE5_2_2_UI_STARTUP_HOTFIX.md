# V3 Phase 5.2.2 — UI Startup Hotfix

Scope: Local UI only. No database schema change.

## Fixed
- Fixed recursive refresh storm: every completed refresh task previously launched more refresh tasks, growing exponentially until the UI could exit/crash.
- Keep QRunnable Python references until completion to avoid premature wrapper collection.
- Delay first refresh until the Qt event loop has started.
- `run_ui.bat` now pauses and shows the exit code if the UI process exits with an error.
- Demo sender defaults changed to five digits: `demo00001@gmail.com` through `demo00020@gmail.com`.

## Unchanged
- Production P5.1 Core and database schema.
- Direct HTTP Exact Template login flow.
- Sender lease/cooldown/random selection logic.
- Existing private templates and local secrets.

## Run
```powershell
.\run.bat version
.\run_ui.bat
```
Expected version: `3.0.0-phase5.2.2-ui-startup-hotfix`.
