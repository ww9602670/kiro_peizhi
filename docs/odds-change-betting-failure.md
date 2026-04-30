# Odds Change Betting Failure

## 2026-04-30

### Symptom

The third-party betting platform can temporarily change odds. When this happens, running strategies may skip betting or mark the submit as failed.

### Evidence

- Server alerts include `odds_changed` events.
- Server `account_odds` showed `account_id=14 / JND28WEB` with `1087` unconfirmed odds rows after an odds change.
- The executor previously treated any unconfirmed stored odds as a hard blocker and returned before `Confirmbet`.
- Existing `succeed=5` handling retried with live odds, but successful retry orders still kept the old stored odds in `bet_orders`.

### Root Causes

1. Odds-change confirmation state was designed for manual awareness, but it was also blocking automated strategies.
2. Runtime odds-change retry did not persist the actual retry odds back to successful orders.
3. If live odds could not rebuild the full batch, partial retry behavior could make local order state diverge from what was actually submitted.

### Fix

- Execution now can use live/latest odds when manual confirmation lags, so temporary odds changes do not block running strategies.
- `succeed=5` retry now returns the rebuilt bet payload, and successful orders store the effective submitted odds.
- Retry now fails the whole batch if live odds are unavailable for any requested key, avoiding false local success.

### Verification

- `python -m pytest backend/tests/test_executor.py -q`
- `python -m compileall backend/app/engine/executor.py backend/app/models/db_ops.py -q`

### Deployment Note

Deploy with the pending multi-account session fixes in one maintenance window, restart the backend, then run a controlled real betting test for an account whose odds have changed.
