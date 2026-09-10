# M WOIF Heart V3 Phase 2.1 — MariaDB last insert id fix

Scope: patch-only fix for Phase 2 storage layer.

## Problem

`storage-smoke` inserted `heart_jobs` successfully, but the repository then tried to fetch the new row using `SELECT LAST_INSERT_ID()` through a new database connection. In MySQL/MariaDB, `LAST_INSERT_ID()` is connection-scoped, so the second connection returned no job row.

Observed error:

```text
job insert succeeded but row was not found
```

## Fix

- Add `MariaDb.insert_get_id(...)`.
- Use the same connection/cursor to read `cursor.lastrowid` after INSERT.
- Fetch inserted rows with explicit primary key values:
  - `heart_jobs.hj_id`
  - `heart_rounds.hround_id`
  - `heart_worker_runs.hwr_id`

## Files

```text
mwoif/storage/database.py
mwoif/storage/repository.py
```

No backup/V1/V2 files are touched.
