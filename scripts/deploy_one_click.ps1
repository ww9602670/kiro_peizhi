#!/usr/bin/env pwsh

[CmdletBinding()]
param(
  [string]$ServerHost = "bocai",
  [string]$RemoteName = "web",
  [string]$MainBranch = "main",
  [string]$LiveBranch = "server/live",
  [string]$ServerRoot = "/opt/bocai_web",
  [string]$SmokeHost = "m.166168sx.com",
  [string]$ReleaseName,
  [switch]$SkipLocalChecks,
  [switch]$SkipBackendImportCheck,
  [switch]$SkipBackendInstall,
  [switch]$SkipFrontendBuild,
  [switch]$SkipPushMain,
  [switch]$SkipLiveMirrorUpdate,
  [switch]$SkipNginxReload,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "release_common.ps1")

$repoRoot = Get-RepoRoot
Set-Location $repoRoot

$powerShellExe = Resolve-FirstCommand -Names @(
  "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
  "powershell.exe"
)
$pythonExe = Resolve-FirstCommand -Names @("python", "py")
$pnpmExe = Resolve-FirstCommand -Names @("pnpm.cmd", "pnpm")
$tarExe = Resolve-FirstCommand -Names @("tar.exe", "tar")
$null = Resolve-FirstCommand -Names @("git")
$null = Resolve-FirstCommand -Names @("ssh")
$null = Resolve-FirstCommand -Names @("scp")

Write-Section "Preflight"
Assert-GitBranch -ExpectedBranch $MainBranch
if (-not $DryRun) {
  Assert-GitClean
}

$commitFull = Get-GitCommit
$commitShort = Get-GitCommit -Short
$timestamp = New-ReleaseTimestamp

if (-not $ReleaseName) {
  $ReleaseName = "{0}_ws_{1}" -f $commitShort, $timestamp
}

$candidateRoot = Join-Path $repoRoot ("release-candidates/{0}/root-app-linux" -f $timestamp)
$archivePath = Join-Path $repoRoot ("release-candidates/{0}/{1}.tar.gz" -f $timestamp, $ReleaseName)
$remoteArchive = "/tmp/{0}.tar.gz" -f $ReleaseName

if (-not $SkipLocalChecks) {
  Write-Section "Local checks"

  if (-not $SkipBackendImportCheck) {
    Invoke-Process `
      -FilePath $pythonExe `
      -ArgumentList @("-c", "import app.main; print('BACKEND_IMPORT_OK')") `
      -WorkingDirectory (Join-Path $repoRoot "backend") `
      -DryRun:$DryRun | Out-Null
  }

  if (-not $SkipFrontendBuild) {
    Invoke-Process `
      -FilePath $pnpmExe `
      -ArgumentList @("build") `
      -WorkingDirectory (Join-Path $repoRoot "frontend") `
      -DryRun:$DryRun | Out-Null
  }
}

Write-Section "Build release candidate"
Invoke-Process `
  -FilePath $powerShellExe `
  -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    (Join-Path $repoRoot "scripts/prepare_linux_release_candidate.ps1"),
    "-OutputRoot",
    $candidateRoot,
    "-IncludeFrontendDist"
  ) `
  -WorkingDirectory $repoRoot `
  -DryRun:$DryRun | Out-Null

if (-not $DryRun) {
  $metadata = [ordered]@{
    release_name = $ReleaseName
    commit = $commitFull
    branch = $MainBranch
    remote = $RemoteName
    created_at = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
  } | ConvertTo-Json

  $metadataPath = Join-Path $candidateRoot ".deploy-metadata.json"
  Set-Content -LiteralPath $metadataPath -Value $metadata -Encoding UTF8
}

Write-Section "Archive release candidate"
Invoke-Process `
  -FilePath $tarExe `
  -ArgumentList @("-czf", $archivePath, "-C", $candidateRoot, ".") `
  -WorkingDirectory $repoRoot `
  -DryRun:$DryRun | Out-Null

if (-not $SkipPushMain) {
  Write-Section "Push deploy commit to remote main"
  Invoke-Process `
    -FilePath "git" `
    -ArgumentList @("push", $RemoteName, ("HEAD:refs/heads/{0}" -f $MainBranch)) `
    -DryRun:$DryRun | Out-Null
}

Write-Section "Upload archive to server"
Invoke-ScpUpload -LocalPath $archivePath -ServerHost $ServerHost -RemotePath $remoteArchive -DryRun:$DryRun

$reloadNginxValue = "true"
if ($SkipNginxReload) {
  $reloadNginxValue = "false"
}

$installBackendValue = "true"
if ($SkipBackendInstall) {
  $installBackendValue = "false"
}

$deployMetadata = [ordered]@{
  release_name = $ReleaseName
  commit = $commitFull
  branch = $MainBranch
  remote = $RemoteName
  created_at = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
} | ConvertTo-Json -Compress
$metadataBase64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($deployMetadata))

$remoteScript = @'
set -euo pipefail

server_root="__SERVER_ROOT__"
release_name="__RELEASE_NAME__"
remote_archive="__REMOTE_ARCHIVE__"
smoke_host="__SMOKE_HOST__"
reload_nginx="__RELOAD_NGINX__"
install_backend="__INSTALL_BACKEND__"
metadata_b64="__METADATA_B64__"

current_target="$(readlink -f "$server_root/current")"
release_dir="$server_root/releases/$release_name"
backup_ts="$(date +%Y%m%d_%H%M%S)"
backup_dir="$server_root/shared/backups/$backup_ts"
venv_python="$server_root/.venv/bin/python"

if [ -e "$release_dir" ]; then
  echo "ERROR=release directory already exists: $release_dir"
  exit 1
fi

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

sudo mkdir -p "$release_dir"
sudo tar -xzf "$remote_archive" -C "$release_dir"
printf '%s' "$metadata_b64" | base64 -d | sudo tee "$release_dir/.deploy-metadata.json" >/dev/null
sudo chown -R bocai:bocai "$release_dir"

if [ "$install_backend" = "true" ]; then
  sudo "$venv_python" -m pip install --force-reinstall "$release_dir/backend"
fi

sudo ln -sfn "$release_dir" "$server_root/current"
sudo systemctl restart bocai-backend

if [ "$reload_nginx" = "true" ]; then
  sudo systemctl reload nginx || sudo nginx -s reload || true
fi

health_body="$(curl -fsS http://127.0.0.1:8888/api/v1/health)"
https_status=""
if [ -n "$smoke_host" ]; then
  https_status="$(curl -k -I -H "Host: $smoke_host" https://127.0.0.1/ 2>/dev/null | head -n 1 || true)"
fi

rm -f "$remote_archive"

printf 'PREVIOUS_RELEASE=%s\n' "$current_target"
printf 'CURRENT_RELEASE=%s\n' "$(readlink -f "$server_root/current")"
printf 'BACKUP_DIR=%s\n' "$backup_dir"
printf 'HEALTH_BODY=%s\n' "$health_body"
printf 'HTTPS_STATUS=%s\n' "$https_status"
'@

$remoteScript = $remoteScript.Replace("__SERVER_ROOT__", $ServerRoot)
$remoteScript = $remoteScript.Replace("__RELEASE_NAME__", $ReleaseName)
$remoteScript = $remoteScript.Replace("__REMOTE_ARCHIVE__", $remoteArchive)
$remoteScript = $remoteScript.Replace("__SMOKE_HOST__", $SmokeHost)
$remoteScript = $remoteScript.Replace("__RELOAD_NGINX__", $reloadNginxValue)
$remoteScript = $remoteScript.Replace("__INSTALL_BACKEND__", $installBackendValue)
$remoteScript = $remoteScript.Replace("__METADATA_B64__", $metadataBase64)

Write-Section "Deploy on server"
$deployOutput = Invoke-RemoteBash -ServerHost $ServerHost -Script $remoteScript -DryRun:$DryRun
$deployInfo = Parse-KeyValueOutput -Lines $deployOutput

if (-not $SkipLiveMirrorUpdate) {
  Write-Section "Update server/live mirror branch"
  Update-LiveMirrorBranch -RemoteName $RemoteName -LiveBranch $LiveBranch -Commit $commitFull -DryRun:$DryRun
}

Write-Section "Summary"
Write-Host ("Release:          {0}" -f $ReleaseName)
Write-Host ("Commit:           {0}" -f $commitFull)
if ($deployInfo.ContainsKey("PREVIOUS_RELEASE")) {
  Write-Host ("Previous release: {0}" -f $deployInfo["PREVIOUS_RELEASE"])
}
if ($deployInfo.ContainsKey("CURRENT_RELEASE")) {
  Write-Host ("Current release:  {0}" -f $deployInfo["CURRENT_RELEASE"])
}
if ($deployInfo.ContainsKey("BACKUP_DIR")) {
  Write-Host ("Backup dir:       {0}" -f $deployInfo["BACKUP_DIR"])
}
if ($deployInfo.ContainsKey("HTTPS_STATUS")) {
  Write-Host ("HTTPS status:     {0}" -f $deployInfo["HTTPS_STATUS"])
}
Write-Host "Deploy finished."
