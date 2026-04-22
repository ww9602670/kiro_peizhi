# Standard Release Process

## Goal

Use one fixed path from local development to server deployment so code changes do not pile up into one large cleanup at the end.

This process assumes the production server uses:

- `/opt/bocai_web/releases/<commit>`
- `/opt/bocai_web/current`
- `/opt/bocai_web/shared/backend.env`
- `/opt/bocai_web/shared/backend-data/bocai.db`
- `/opt/bocai_web/shared/data/jnd28.sqlite3`

## Daily development rule

1. Develop on a feature branch, not directly in the long-lived production branch.
2. Keep each change small enough to verify on the same day.
3. If backend data structures change, add the migration plan in the same batch.
4. Do not let local runtime files, reports, probes, or ad hoc scripts mix into the release scope.

## Before Git

Every batch that may go to production must pass this local gate:

1. Confirm release scope.
   Use `scripts/prepare_linux_release_candidate.ps1` to generate a clean candidate from root `backend` + root `frontend` + `deploy/linux`.
2. Verify backend startup import.
   Example: from `backend`, run `python -c "import app.main; print('BACKEND_IMPORT_OK')"`
3. Verify frontend build.
   Example: from `frontend`, run `pnpm build`
4. If the schema changed, prepare a migration note and a rollback note before pushing.
5. Freeze the batch.
   Do not keep mixing unrelated work after the candidate has been verified.

## Git standard

1. Commit the release batch in logical groups.
   Recommended split:
   - app code
   - migration or deployment changes
   - docs and reports
2. Push the verified branch.
3. Open a review or release note that states:
   - target server baseline
   - schema change yes or no
   - rollback method
   - smoke-check scope

## Server pre-deploy gate

Before touching production:

1. Confirm which server version is live.
2. Confirm whether shared database schema is compatible.
3. Back up `/opt/bocai_web/shared/backend-data/bocai.db`.
4. Back up `/opt/bocai_web/shared/backend.env`.
5. If there is a schema change, rehearse the migration on a copied database first.
6. Confirm `BOCAI_ALLOW_LEGACY_DESTRUCTIVE_RESET=false` in production before restart.

No production deploy should continue if any of those six checks are missing.

For the current legacy-to-current schema jump, use:

- `python scripts/migrate_legacy_platform_schema.py --source-db <legacy.db> --target-db <migrated.db>`
- `pwsh scripts/rehearse_legacy_platform_migration.ps1 -SourceDb <legacy.db>`

## Standard deploy steps

1. Generate or locate the release candidate locally.
2. Put it on the server under `/opt/bocai_web/releases/<commit>`.
3. Install backend dependencies for that release using the shared `.venv`.
4. Install frontend dependencies and build `frontend/dist` for that release if the candidate did not already include `dist`.
5. Run migration only after backup and rehearsal.
6. Switch `/opt/bocai_web/current` to the new release.
7. Restart `bocai-backend`.
8. Reload nginx.
9. Run smoke checks:
   - `/api/v1/health`
   - login
   - operator account list
   - strategy list
   - bet order list
   - countdown page

## Rollback standard

If the code is bad but the schema is unchanged:

1. switch `current` back to the previous release
2. restart `bocai-backend`
3. reload nginx

If the schema changed:

1. switch `current` back only after understanding schema compatibility
2. restore the shared database backup if needed
3. re-run smoke checks on the restored state

## Prohibited actions

- do not `git pull` directly on the production working tree as the deployment method
- do not deploy from a dirty local workspace
- do not ship local `backend/data/bocai.db`
- do not deploy a schema-changing release without a backup
- do not deploy frontend-only or backend-only if the API contract changed on both sides
- do not set `BOCAI_ALLOW_LEGACY_DESTRUCTIVE_RESET=true` on production

## Release cadence recommendation

To avoid another large backlog:

1. make one small verified release batch every day or every two days during active development
2. when a feature is larger than one batch, keep it behind a flag or on a branch until complete
3. treat any uncommitted local-only state older than two days as cleanup debt

## Current project note

This project currently has a schema split between the live server baseline and the local upload candidate. Because of that, the next production deployment must be treated as:

- code release
- schema migration
- data retention operation

not as a simple code sync.
