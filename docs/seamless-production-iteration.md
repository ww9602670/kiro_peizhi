# Seamless Production Iteration

Date: 2026-04-22

## Current Baseline

Current production is not a clean Git release.

- Server release path: `/opt/bocai_web/releases/3b567be_ws_20260422_074618`
- Local Git `HEAD`: `3b567be`
- Local workspace state when checked now:
  - tracked changes: `108`
  - untracked paths: `209`

Conclusion:

- The server is functionally based on a local workspace snapshot created from `HEAD 3b567be`
- But it is not reproducible from Git alone, because the deployed release id is a workspace snapshot (`_ws_...`), not a clean commit/tag release

## Why Local And Server Are Not Truly In Sync

They are only "snapshot-aligned", not "release-aligned".

- The deployed package came from the local workspace, not from a clean Git tag
- The local workspace still contains a large amount of non-release noise
- The server release name cannot be recreated by checking out a single Git ref
- Future updates will keep becoming manual cleanup exercises unless the local workflow changes

## Required Local Adjustments

To make future updates iterate cleanly to Linux production, the local repo must follow these rules:

1. Release only from a clean Git commit
   - No more production releases from a dirty workspace snapshot
   - Each production release must map to one commit SHA or tag

2. Keep production code boundary fixed
   - Release scope: `backend/`, `frontend/`, `deploy/linux/`, `scripts/`
   - Do not mix in `reports/`, `.codex-runtime/`, `.codex-release/`, local probes, temp files, or unrelated `saas/` work

3. Treat database migration as part of the release
   - Any schema change must include:
     - migration script
     - local rehearsal
     - server backup-copy rehearsal
   - No schema-dependent code may go live before this is ready

4. Use one production source of truth
   - Pick one canonical production branch/remote
   - Server release must come from that source, not from whichever local workspace happens to be on the machine

5. Preserve Linux release architecture
   - Keep `releases/<release-id>`
   - Keep `/opt/bocai_web/current` symlink switch
   - Keep shared DB/env outside the release directory

6. Freeze running-strategy handling before deploy
   - Snapshot any `running` strategy
   - Decide whether it should be restored, stopped, or migrated
   - Do not let deployment accidentally resume a strategy with unknown in-memory Martin state

## Standard Iteration Workflow

1. Start from clean branch
   - Pull the canonical production branch
   - Create a short-lived feature/release branch

2. Make a small batch of changes
   - Avoid mixing infrastructure, UI, migration, and experimental work in one batch

3. Run local gate checks
   - backend import/tests
   - frontend build/tests
   - packaging check

4. Generate release candidate from the clean commit
   - Release id should use the commit SHA
   - Do not use `_ws_` workspace-only release ids for production

5. Rehearse migration
   - local old-schema rehearsal
   - real server backup-copy rehearsal

6. Deploy to Linux release directory
   - upload package to `/opt/bocai_web/releases/<commit>`
   - backup live DB
   - place migrated DB
   - switch `/opt/bocai_web/current`
   - restart service

7. Run smoke checks
   - backend health
   - nginx frontend response
   - critical operator login
   - strategy list / order list / countdown page

8. Record rollback point
   - backup DB path
   - previous release path
   - env backup path

## Immediate Repo Changes Needed Before The Next Release

- Commit the current deploy-capable code as a real Git baseline
- Keep generated reports and runtime artifacts out of the release path
- Continue using:
  - [standard-release-process.md](/H:/d/bocai_web/docs/standard-release-process.md)
  - [linux-deployment.md](/H:/d/bocai_web/docs/linux-deployment.md)
- Stop treating the local workspace itself as the production artifact

## Practical Rule

If a future server version cannot be reproduced by:

- checking out one Git commit
- running the release packaging script
- running the documented migration rehearsal

then it is not ready for production deployment.
