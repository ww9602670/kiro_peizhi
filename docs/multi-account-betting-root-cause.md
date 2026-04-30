# Multi-Account Betting Root Cause

## 2026-04-29

### Symptom

An operator can bind multiple betting accounts and create strategies for those accounts, but after starting strategies some betting windows produce no real platform order.

### Evidence

- Strategy `id=78` for account `21` started and placed successful real bets for issues `3426541` and `3426543`.
- Later windows still reached the pre-submit phase, but every signal was rejected by risk check with reason `session_token`.
- Account `21` had `account_platform_sessions.platform_type=JND282`, `status=reconnecting`, and no persisted session token while its worker lock was still active.
- A live adapter probe showed that `create_platform_adapter("JND282", custom_url)` created an adapter whose `lottery_type` was incorrectly `JND28WEB`.

### Root Causes

1. `JNDAdapter` fell back to `JND28WEB` whenever a custom platform URL was provided, even when the requested strategy platform was `JND282`.
2. `SessionManager._reconnect()` cleared the persisted platform session token before replacement login completed. If this happened near a betting window, `RiskController` rejected all signals because the DB token was empty.

### Fix

- Preserve the requested JND platform type as the adapter `lottery_type`, including when a custom URL is used.
- During reconnect, mark the platform session as `reconnecting` without clearing the existing token first; clear it only after reconnect/login has definitively failed.
- Keep `RiskController` using `account_platform_sessions(account_id, platform_type)` so each account/platform remains isolated.

### Deployment Note

The local code fix is covered by targeted tests. Server deployment requires copying the changed files and restarting the backend; do this only in a maintenance window or after confirming active workers can tolerate the restart.

### Current Status

- Code-level fix: completed locally and covered by targeted tests.
- Server acceptance: not yet completed because the backend was not restarted while active strategies were running.
- Treat this issue as pending production verification until a maintenance-window deploy, backend restart, and controlled real multi-account betting test complete.
