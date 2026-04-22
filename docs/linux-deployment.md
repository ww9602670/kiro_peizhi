# Linux Deployment

## Deployment shape

Production should use a release-based layout instead of `git pull` in place:

```text
/opt/bocai_web
  |- .venv/
  |- current -> /opt/bocai_web/releases/<commit>
  |- releases/
  |    \- <commit>/
  |         |- backend/
  |         |- frontend/
  |         \- deploy/linux/
  \- shared/
       |- backend.env
       |- backend-data/bocai.db
       \- data/jnd28.sqlite3
```

Rules:

- `current` always points to the active release.
- runtime data lives under `shared/`, not inside a release directory
- backend runs from `/opt/bocai_web/current/backend`
- nginx serves static files from `/opt/bocai_web/current/frontend/dist`
- do not deploy with `git pull --ff-only` on the server

## Runtime files

Use these production paths:

- `BOCAI_DB_PATH=/opt/bocai_web/shared/backend-data/bocai.db`
- `BOCAI_HISTORY_DB_PATH=/opt/bocai_web/shared/data/jnd28.sqlite3`
- `EnvironmentFile=/opt/bocai_web/shared/backend.env`

Recommended production values:

```bash
BOCAI_ENV=production
BOCAI_JWT_SECRET=replace-with-a-long-random-secret
BOCAI_DB_PATH=/opt/bocai_web/shared/backend-data/bocai.db
BOCAI_HISTORY_DB_PATH=/opt/bocai_web/shared/data/jnd28.sqlite3
BOCAI_DEFAULT_ADMIN_ENABLED=false
BOCAI_ALLOW_LEGACY_DESTRUCTIVE_RESET=false
BOCAI_RESTORE_WORKERS_ON_STARTUP=false
BOCAI_TRUSTED_HOSTS=your-domain.com,www.your-domain.com
BOCAI_CORS_ORIGINS=
```

## systemd service

The service should point at the shared env file and the active release:

```ini
[Service]
WorkingDirectory=/opt/bocai_web/current/backend
EnvironmentFile=/opt/bocai_web/shared/backend.env
ExecStart=/opt/bocai_web/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8888
```

The repo template is `deploy/linux/bocai-backend.service`.

## First-time server setup

1. Create shared directories:

```bash
sudo mkdir -p /opt/bocai_web/releases
sudo mkdir -p /opt/bocai_web/shared/backend-data
sudo mkdir -p /opt/bocai_web/shared/data
```

2. Create the virtual environment once:

```bash
cd /opt/bocai_web
python3 -m venv .venv
source /opt/bocai_web/.venv/bin/activate
python -m pip install --upgrade pip
```

3. Copy `deploy/linux/backend.env.example` to `/opt/bocai_web/shared/backend.env` and set real secrets.

4. Install the systemd unit from `deploy/linux/bocai-backend.service`.

5. Install nginx and point the site root at `/opt/bocai_web/current/frontend/dist`.

## Release deployment

Use the standard release process in [standard-release-process.md](./standard-release-process.md).

The short version is:

1. Prepare a release candidate locally.
2. Back up `/opt/bocai_web/shared/backend-data/bocai.db` before any migration.
3. Upload the candidate into `/opt/bocai_web/releases/<commit>`.
4. Install backend/frontend dependencies for that release.
5. Run any required migration against a copied database first.
6. Build the frontend inside that release if `dist` was not shipped.
7. Switch `current` to the new release.
8. Restart `bocai-backend` and reload nginx.
9. Run smoke checks.

## Smoke checks

Backend:

```bash
curl http://127.0.0.1:8888/api/v1/health
```

Through nginx:

```bash
curl http://your-domain.com/api/v1/health
curl -I http://your-domain.com/
```

Expected backend response:

```json
{"code":0,"message":"success","data":{"status":"ok","service":"bocai-backend"}}
```

## Rollback

If the new code fails and the database schema was not changed:

1. switch `current` back to the previous release
2. restart `bocai-backend`
3. reload nginx

If a migration changed shared data, rollback also requires restoring the database backup.

## Operational notes

- `BOCAI_DEFAULT_ADMIN_ENABLED=false` must stay disabled in production.
- `BOCAI_ALLOW_LEGACY_DESTRUCTIVE_RESET=false` must stay disabled in production.
- production account data belongs to the shared database, not to the release package
- do not ship local `backend/data/bocai.db` from a developer machine
- when the schema changes, do not deploy before a migration and backup plan exist
