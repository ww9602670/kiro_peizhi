# One-Click Release And Rollback

## Goal

Provide one fixed way to:

1. push a verified local version to Git
2. deploy it to the Linux server
3. keep one branch always equal to the live server
4. roll back quickly when needed

## Branch meaning

Use these branches with fixed roles:

- `main`
  - next deployable code line
- `server/live`
  - exact mirror of what the production server is currently running
- `cleanup/*`
  - repo cleanup or non-production maintenance work

Normal rule:

1. finish code changes on `main`
2. run one-click deploy
3. only after deployment succeeds, update `server/live`

## Scripts

- [scripts/deploy_one_click.ps1](/H:/d/bocai_web/scripts/deploy_one_click.ps1)
- [scripts/rollback_one_click.ps1](/H:/d/bocai_web/scripts/rollback_one_click.ps1)

## One-Click Deploy

Default command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_one_click.ps1
```

What it does:

1. requires current branch to be `main`
2. requires a clean working tree for a real deploy
3. runs backend import check
4. runs frontend build
5. generates a Linux release candidate with `frontend/dist`
6. creates a release archive
7. pushes `HEAD` to `web/main`
8. uploads the archive to the server
9. creates a new `/opt/bocai_web/releases/<release>`
10. backs up:
   - current release pointer
   - `backend.env`
   - production SQLite database
11. syncs the shared Python environment with the new `backend/` package
12. switches `/opt/bocai_web/current`
13. restarts `bocai-backend`
14. reloads nginx
15. checks backend health and HTTPS
16. moves `web/server/live` to the deployed commit

### Useful options

Skip local checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_one_click.ps1 -SkipLocalChecks
```

Skip nginx reload:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_one_click.ps1 -SkipNginxReload
```

Skip backend package reinstall:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_one_click.ps1 -SkipBackendInstall
```

Dry run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_one_click.ps1 -DryRun
```

Dry run still checks the branch, but it does not block on a dirty working tree because it does not actually push or deploy.

## One-Click Rollback

Rollback to the previous release:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_one_click.ps1
```

What it does:

1. chooses the previous release automatically if no target is given
2. creates a fresh backup of the current production database before rollback
3. switches `/opt/bocai_web/current` to the target release
4. restarts `bocai-backend`
5. reloads nginx
6. checks backend health and HTTPS
7. moves `web/server/live` back to the rolled-back commit

### Roll back to a specific release

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_one_click.ps1 -TargetRelease 3b567be_ws_20260422_074618
```

### Roll back code and restore a database backup

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_one_click.ps1 -RestoreBackupDir 20260422_012821
```

The restore source is chosen in this order:

1. `live_bocai.db`
2. `bocai.db`
3. `bocai.migrated.db`

### Dry run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_one_click.ps1 -DryRun
```

## Safety Notes

- do not run one-click deploy from a dirty working tree
- do not deploy from a branch other than `main`
- keep `BOCAI_ALLOW_LEGACY_DESTRUCTIVE_RESET=false` in production
- use database restore only when you know the rollback needs data restoration, not only code restoration
- `server/live` is allowed to move backward during rollback because it is a mirror branch, not a development branch

## Expected Result

After successful deploy:

- `web/main` = deployed commit
- `web/server/live` = deployed commit
- server `/opt/bocai_web/current` = deployed release

After successful rollback:

- `web/main` stays as the latest mainline
- `web/server/live` moves back to the actual rolled-back server commit
- server `/opt/bocai_web/current` = rollback target release
