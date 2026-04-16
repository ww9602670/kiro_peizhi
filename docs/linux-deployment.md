# Linux deployment

## Summary

This project should be deployed as:

- `frontend/dist` served by nginx
- FastAPI backend running as a single `uvicorn` process behind nginx
- environment variables supplied by `deploy/linux/backend.env`

Do not run multiple backend workers. The current backend keeps worker/session state in memory.

## Build

Frontend:

```bash
cd frontend
npm install
npm run build
```

Backend:

```bash
cd backend
python3 -m pip install -e .
```

## Runtime env

Copy `deploy/linux/backend.env.example` to `deploy/linux/backend.env` and update:

- `BOCAI_JWT_SECRET`
- `BOCAI_DB_PATH`
- `BOCAI_HISTORY_DB_PATH`
- `BOCAI_TRUSTED_HOSTS`

Recommended defaults in production:

- `BOCAI_DEFAULT_ADMIN_ENABLED=false`
- `BOCAI_RESTORE_WORKERS_ON_STARTUP=false`

## systemd

Install `deploy/linux/bocai-backend.service` to `/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable bocai-backend
sudo systemctl restart bocai-backend
sudo systemctl status bocai-backend
```

## nginx

Install `deploy/linux/nginx.conf` as a site config, adjust `server_name`, then reload nginx.

## Notes

- `frontend/.env.production` keeps browser requests on the same origin via `/api/v1`.
- `BOCAI_HISTORY_DB_PATH` must point to the external history SQLite file used by backtest/history sync.
- If you intentionally want workers restored after a restart, set `BOCAI_RESTORE_WORKERS_ON_STARTUP=true`.
