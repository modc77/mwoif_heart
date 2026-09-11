# V3 Phase 5.4.1.1 Init Import Hotfix

Patch-only hotfix for startup regression introduced by Phase 5.4.1.

## Fix
Restores `__app_name__` in `mwoif/__init__.py` while keeping version `3.0.0-phase5.4.1-progress-hotfix`.

## Scope
- No DB schema changes.
- No runtime/heart/login logic changes.
- No UI changes.
- Only restores package metadata required by `mwoif.cli` imports.
