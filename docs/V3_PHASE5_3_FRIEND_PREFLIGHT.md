# M WOIF Heart V3 — Phase 5.3 Friend Capacity Preflight

## Scope
Preflight gate for the Local production prototype before every Heart Job or resumed Job.

- Receiver logs in once with Direct HTTP Exact Template.
- Core calls `FriendAPI/ListFriends` before any Sender work.
- Friend capacity is treated as 300.
- UI reserves `min(remaining hearts, configured friend slots)`; default 50.
- The same Receiver auth/session object is reused by the Fast Batch runner; there is no second Receiver login.
- If enough slots exist, the job starts immediately.
- If slots are insufficient, the UI shows the exact number that must be deleted and waits for operator confirmation.
- Confirm: remove only the exact number required, re-check capacity, then start.
- Cancel: remove nothing, cancel the prepared job, purge its temporary Receiver credential and stop.
- Resuming later always performs the preflight again.

## Fail-closed parser rule
The project still has no generated Python protobuf class for `ListFriends`. Phase 5.3 therefore scans the real protobuf response and selects the repeated player-id field path.

The important production rule is fail-closed:

- empty gRPC body => verified empty friend list;
- parsed response with medium/high confidence => friend count/player IDs may be used;
- non-empty response that cannot be parsed confidently => **do not start the job and do not delete anything**.

This prevents an unknown response shape from being mistaken for `0/300` friends.

## Database
No new schema migration in Phase 5.3. It uses the Phase 5.1 `heart_*` production tables for jobs, leases, attempts and sender/receiver cooldowns.

## UI flow
1. Enter Receiver email/password and heart amount.
2. Press Start.
3. Receiver logs in once and friend capacity is checked.
4. If enough slots: Fast Batch starts using the same Receiver session.
5. If insufficient: confirmation dialog shows current friends, free slots, required slots and exact delete count.
6. Confirm => remove exact count => re-check => start Fast Batch.
7. Cancel => no deletion, no Sender work.

## Integration
This preflight is part of the Phase 5.3 Fast Batch + Real-time engine. It is not a separate sequential runner.
