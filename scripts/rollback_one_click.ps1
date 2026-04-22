#!/usr/bin/env pwsh

[CmdletBinding()]
param(
  [string]$ServerHost = "bocai",
  [string]$RemoteName = "web",
  [string]$LiveBranch = "server/live",
  [string]$ServerRoot = "/opt/bocai_web",
  [string]$SmokeHost = "m.166168sx.com",
  [string]$TargetRelease,
  [string]$RestoreBackupDir,
  [switch]$SkipLiveMirrorUpdate,
  [switch]$SkipNginxReload,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "release_common.ps1")

$repoRoot = Get-RepoRoot
Set-Location $repoRoot

$null = Resolve-FirstCommand -Names @("git")
$null = Resolve-FirstCommand -Names @("ssh")

Write-Section "Preflight"

$reloadNginxValue = "true"
if ($SkipNginxReload) {
  $reloadNginxValue = "false"
}

$requestedTarget = if ($TargetRelease) { $TargetRelease } else { "" }
$requestedBackup = if ($RestoreBackupDir) { $RestoreBackupDir } else { "" }

$remoteScript = @'
set -euo pipefail

server_root="__SERVER_ROOT__"
target_release="__TARGET_RELEASE__"
restore_backup_dir="__RESTORE_BACKUP_DIR__"
smoke_host="__SMOKE_HOST__"
reload_nginx="__RELOAD_NGINX__"

resolve_previous_release() {
  local current_target
  current_target="$(readlink -f "$server_root/current")"
  for candidate in $(ls -1dt "$server_root/releases"/* 2>/dev/null); do
    if [ "$(readlink -f "$candidate")" != "$current_target" ]; then
      basename "$candidate"
      return 0
    fi
  done
  return 1
}

current_target="$(readlink -f "$server_root/current")"
current_name="$(basename "$current_target")"

if [ -z "$target_release" ]; then
  target_release="$(resolve_previous_release)"
fi

if [ -z "$target_release" ]; then
  echo "ERROR=no rollback target found"
  exit 1
fi

target_dir="$server_root/releases/$target_release"
if [ ! -d "$target_dir" ]; then
  echo "ERROR=rollback target missing: $target_dir"
  exit 1
fi

if [ "$(readlink -f "$target_dir")" = "$current_target" ]; then
  echo "ERROR=rollback target is already current"
  exit 1
fi

backup_ts="$(date +%Y%m%d_%H%M%S)"
backup_dir="$server_root/shared/backups/$backup_ts"
sudo mkdir -p "$backup_dir"
printf '%s\n' "$current_target" | sudo tee "$backup_dir/current_release.txt" >/dev/null
sudo cp "$server_root/shared/backend.env" "$backup_dir/backend.env"

export SOURCE_DB="$server_root/shared/backend-data/bocai.db"
export BACKUP_DB="$backup_dir/live_bocai.db"
sudo -E python3 - <<'PY'
import os
import sqlite3

src = os.environ["SOURCE_DB"]
dst = os.environ["BACKUP_DB"]
src_conn = sqlite3.connect(src)
dst_conn = sqlite3.connect(dst)
try:
    src_conn.backup(dst_conn)
finally:
    dst_conn.close()
    src_conn.close()
PY

restored_from=""
if [ -n "$restore_backup_dir" ]; then
  candidate_base="$server_root/shared/backups/$restore_backup_dir"
  if [ ! -d "$candidate_base" ]; then
    echo "ERROR=restore backup directory missing: $candidate_base"
    exit 1
  fi

  restore_source=""
  for name in live_bocai.db bocai.db bocai.migrated.db; do
    if [ -f "$candidate_base/$name" ]; then
      restore_source="$candidate_base/$name"
      break
    fi
  done

  if [ -z "$restore_source" ]; then
    echo "ERROR=no SQLite backup file found in: $candidate_base"
    exit 1
  fi

  export RESTORE_SOURCE="$restore_source"
  export TARGET_DB="$server_root/shared/backend-data/bocai.db"
  sudo -E python3 - <<'PY'
import os
import sqlite3

src = os.environ["RESTORE_SOURCE"]
dst = os.environ["TARGET_DB"]
src_conn = sqlite3.connect(src)
dst_conn = sqlite3.connect(dst)
try:
    src_conn.backup(dst_conn)
finally:
    dst_conn.close()
    src_conn.close()
PY
  restored_from="$restore_source"
fi

sudo ln -sfn "$target_dir" "$server_root/current"
sudo systemctl restart bocai-backend

if [ "$reload_nginx" = "true" ]; then
  sudo systemctl reload nginx || sudo nginx -s reload || true
fi

health_body="$(curl -fsS http://127.0.0.1:8888/api/v1/health)"
https_status=""
if [ -n "$smoke_host" ]; then
  https_status="$(curl -k -I -H "Host: $smoke_host" https://127.0.0.1/ 2>/dev/null | head -n 1 || true)"
fi

target_commit="$(
  python3 - "$target_dir" <<'PY'
import json
import os
import re
import sys

release_dir = sys.argv[1]
meta_path = os.path.join(release_dir, ".deploy-metadata.json")
if os.path.exists(meta_path):
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        commit = data.get("commit")
        if commit:
            print(commit)
            raise SystemExit(0)
    except Exception:
        pass

candidate_path = os.path.join(release_dir, "RELEASE-CANDIDATE.md")
if os.path.exists(candidate_path):
    text = open(candidate_path, "r", encoding="utf-8", errors="replace").read()
    match = re.search(r"- head: ([0-9a-f]{7,40})", text)
    if match:
        print(match.group(1))
        raise SystemExit(0)

name = os.path.basename(release_dir)
match = re.match(r"([0-9a-f]{7,40})(?:_|$)", name)
if match:
    print(match.group(1))
PY
)"

printf 'PREVIOUS_RELEASE=%s\n' "$current_name"
printf 'ROLLED_BACK_TO=%s\n' "$(basename "$(readlink -f "$server_root/current")")"
printf 'BACKUP_DIR=%s\n' "$backup_dir"
printf 'RESTORED_FROM=%s\n' "$restored_from"
printf 'TARGET_COMMIT=%s\n' "$target_commit"
printf 'HEALTH_BODY=%s\n' "$health_body"
printf 'HTTPS_STATUS=%s\n' "$https_status"
'@

$remoteScript = $remoteScript.Replace("__SERVER_ROOT__", $ServerRoot)
$remoteScript = $remoteScript.Replace("__TARGET_RELEASE__", $requestedTarget)
$remoteScript = $remoteScript.Replace("__RESTORE_BACKUP_DIR__", $requestedBackup)
$remoteScript = $remoteScript.Replace("__SMOKE_HOST__", $SmokeHost)
$remoteScript = $remoteScript.Replace("__RELOAD_NGINX__", $reloadNginxValue)

Write-Section "Rollback on server"
$rollbackOutput = Invoke-RemoteBash -ServerHost $ServerHost -Script $remoteScript -DryRun:$DryRun
$rollbackInfo = Parse-KeyValueOutput -Lines $rollbackOutput

if (-not $SkipLiveMirrorUpdate -and $rollbackInfo.ContainsKey("TARGET_COMMIT") -and $rollbackInfo["TARGET_COMMIT"]) {
  Write-Section "Update server/live mirror branch"
  Update-LiveMirrorBranch -RemoteName $RemoteName -LiveBranch $LiveBranch -Commit $rollbackInfo["TARGET_COMMIT"] -DryRun:$DryRun
}

Write-Section "Summary"
if ($rollbackInfo.ContainsKey("PREVIOUS_RELEASE")) {
  Write-Host ("Previous current: {0}" -f $rollbackInfo["PREVIOUS_RELEASE"])
}
if ($rollbackInfo.ContainsKey("ROLLED_BACK_TO")) {
  Write-Host ("Rolled back to:   {0}" -f $rollbackInfo["ROLLED_BACK_TO"])
}
if ($rollbackInfo.ContainsKey("BACKUP_DIR")) {
  Write-Host ("Backup dir:       {0}" -f $rollbackInfo["BACKUP_DIR"])
}
if ($rollbackInfo.ContainsKey("RESTORED_FROM") -and $rollbackInfo["RESTORED_FROM"]) {
  Write-Host ("DB restored from: {0}" -f $rollbackInfo["RESTORED_FROM"])
}
if ($rollbackInfo.ContainsKey("TARGET_COMMIT") -and $rollbackInfo["TARGET_COMMIT"]) {
  Write-Host ("Live mirror now:  {0}" -f $rollbackInfo["TARGET_COMMIT"])
}
if ($rollbackInfo.ContainsKey("HTTPS_STATUS")) {
  Write-Host ("HTTPS status:     {0}" -f $rollbackInfo["HTTPS_STATUS"])
}
Write-Host "Rollback finished."
