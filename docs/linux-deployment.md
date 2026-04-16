# Linux deployment

## Architecture

Deploy the project in this shape:

- nginx serves `frontend/dist`
- nginx proxies `/api/` to FastAPI on `127.0.0.1:8888`
- backend runs as a single `uvicorn` process under `systemd`
- backend runtime env comes from `deploy/linux/backend.env`

Do not run multiple backend workers. The current backend keeps worker/session state in memory.

## Server requirements

Recommended baseline:

- Ubuntu 22.04 or 24.04
- 2 vCPU / 4 GB RAM minimum
- Python 3.10+
- Node.js 20+
- nginx
- git

Install system packages:

```bash
sudo apt update
sudo apt install -y git nginx python3 python3-venv python3-pip curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo corepack enable
```

## Private repository access

If the server needs to pull from the private GitHub repository, configure SSH first:

```bash
ssh-keygen -t ed25519 -C "deploy@your-server"
cat ~/.ssh/id_ed25519.pub
```

Add that public key to GitHub:

- either as a deploy key on `ww9602670/web`
- or on the GitHub account that owns the server pull access

Verify access:

```bash
ssh -T git@github.com
```

## Directory layout

Recommended layout:

```text
/opt/bocai_web
  |- backend/
  |- frontend/
  |- deploy/linux/
  |- data/
  \- .venv/
```

Create the base directory:

```bash
sudo mkdir -p /opt/bocai_web
sudo chown -R $USER:$USER /opt/bocai_web
cd /opt
git clone git@github.com:ww9602670/web.git bocai_web
cd /opt/bocai_web
```

## Backend setup

Create a virtual environment and install backend dependencies:

```bash
cd /opt/bocai_web
python3 -m venv .venv
source .venv/bin/activate
cd backend
python -m pip install --upgrade pip
python -m pip install -e .
```

Optional verification:

```bash
cd /opt/bocai_web/backend
/opt/bocai_web/.venv/bin/python -c "import app.main; print('BACKEND_IMPORT_OK')"
```

## Frontend setup

This repo contains `pnpm-lock.yaml`, so use pnpm for reproducible installs:

```bash
cd /opt/bocai_web/frontend
pnpm install --frozen-lockfile
pnpm build
```

`frontend/.env.production` is already configured to keep browser requests on the same origin via `/api/v1`.

## Runtime environment

Copy the example env file:

```bash
cd /opt/bocai_web
cp deploy/linux/backend.env.example deploy/linux/backend.env
```

Edit `deploy/linux/backend.env` and set:

- `BOCAI_JWT_SECRET`
- `BOCAI_DB_PATH`
- `BOCAI_HISTORY_DB_PATH`
- `BOCAI_TRUSTED_HOSTS`

Recommended production values:

- `BOCAI_DEFAULT_ADMIN_ENABLED=false`
- `BOCAI_RESTORE_WORKERS_ON_STARTUP=false`
- `BOCAI_CORS_ORIGINS=` empty when frontend and backend are served through the same domain

Example:

```bash
BOCAI_ENV=production
BOCAI_JWT_SECRET=replace-with-a-long-random-secret
BOCAI_DB_PATH=/opt/bocai_web/backend/data/bocai.db
BOCAI_HISTORY_DB_PATH=/opt/bocai_web/data/jnd28.sqlite3
BOCAI_DEFAULT_ADMIN_ENABLED=false
BOCAI_RESTORE_WORKERS_ON_STARTUP=false
BOCAI_TRUSTED_HOSTS=your-domain.com,www.your-domain.com
BOCAI_CORS_ORIGINS=
```

## Initial data files

Make sure the database directories exist:

```bash
mkdir -p /opt/bocai_web/backend/data
mkdir -p /opt/bocai_web/data
```

`BOCAI_HISTORY_DB_PATH` points to the history SQLite used by backtest/history sync.

If you already have a production `jnd28.sqlite3`, upload it to:

```bash
/opt/bocai_web/data/jnd28.sqlite3
```

If you do not have one yet, initialize and inspect it with:

```bash
cd /opt/bocai_web
/opt/bocai_web/.venv/bin/python history_collector.py --status
```

That command will create the file and base tables if they do not exist.

## Initial admin account

Production no longer auto-creates the default admin by default, so bootstrap the first admin explicitly:

```bash
cd /opt/bocai_web/backend
/opt/bocai_web/.venv/bin/python bootstrap_admin.py --username admin --password 'replace-me-now'
```

This script creates or updates the admin user in `BOCAI_DB_PATH`.

## systemd service

Install the service file:

```bash
sudo cp /opt/bocai_web/deploy/linux/bocai-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bocai-backend
sudo systemctl restart bocai-backend
sudo systemctl status bocai-backend
```

Useful commands:

```bash
sudo journalctl -u bocai-backend -f
sudo systemctl restart bocai-backend
sudo systemctl stop bocai-backend
```

## nginx configuration

Install the nginx site config:

```bash
sudo cp /opt/bocai_web/deploy/linux/nginx.conf /etc/nginx/sites-available/bocai_web
sudo ln -sf /etc/nginx/sites-available/bocai_web /etc/nginx/sites-enabled/bocai_web
sudo rm -f /etc/nginx/sites-enabled/default
```

Edit `/etc/nginx/sites-available/bocai_web` and replace:

- `your-domain.com`
- `www.your-domain.com`

Then validate and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## HTTPS

If the domain is already pointed at the server, install TLS with certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

## First start verification

Backend health:

```bash
curl http://127.0.0.1:8888/api/v1/health
```

Through nginx:

```bash
curl http://your-domain.com/api/v1/health
```

Frontend static site:

```bash
curl -I http://your-domain.com/
```

Expected backend response:

```json
{"code":0,"message":"success","data":{"status":"ok","service":"bocai-backend"}}
```

## Update / redeploy

When new commits are pushed:

```bash
cd /opt/bocai_web
git pull --ff-only
source /opt/bocai_web/.venv/bin/activate
cd /opt/bocai_web/backend
python -m pip install -e .
cd /opt/bocai_web/frontend
pnpm install --frozen-lockfile
pnpm build
sudo systemctl restart bocai-backend
sudo systemctl reload nginx
```

## Operational notes

- `BOCAI_RESTORE_WORKERS_ON_STARTUP=false` is safer for the first production deployment. Turn it on only if you explicitly want running workers to auto-resume after a reboot.
- `BOCAI_DEFAULT_ADMIN_ENABLED=false` avoids shipping the historical `admin/admin123` default account into production.
- `BOCAI_JWT_SECRET` is mandatory in production. Backend startup will fail if it is left at the development default.
- Current operator/admin passwords are still stored in plaintext in the app database. Treat the DB file as sensitive and restrict file permissions accordingly.

## Troubleshooting

Frontend build fails:

- confirm Node.js 20+ is installed
- run `corepack enable`
- run `pnpm install --frozen-lockfile` again

Backend service fails to start:

- `sudo journalctl -u bocai-backend -n 200 --no-pager`
- check `BOCAI_JWT_SECRET`
- check `BOCAI_DB_PATH` and `BOCAI_HISTORY_DB_PATH`
- verify `/opt/bocai_web/.venv/bin/python` exists

Health endpoint works locally but not through nginx:

- check `server_name`
- check DNS
- run `sudo nginx -t`
- confirm port 80/443 is open in the firewall/security group
