# Shared Market Data Source Note

## 2026-04-29

- Created shared market data group `id=1`.
- Collector account: `ceshi11`.
- Reused platform URL: `https://8783288200-bty.mm555.co/`.
- Verified both `JND282` and `JND28WEB` login/capability states are online for the account.
- Verified `/api/v1/lottery/current-install?platform_type=JND282` can return `market_data_state=shared_hit` after seeding the shared snapshot.
- Found and patched a field-name mismatch in `backend/app/engine/shared_market_runtime.py`: shared snapshot writes must pass `open_result`, not `pre_result`.
- The patched file was deployed to the server code directory, but the backend was not restarted because strategy `id=78` had an active worker lock at the time. A backend restart is still required for automatic shared snapshot writes to use the patched code.

