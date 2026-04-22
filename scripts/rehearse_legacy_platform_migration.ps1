#!/usr/bin/env pwsh

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$SourceDb,
  [string]$OutputRoot,
  [switch]$Overwrite
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$resolvedSource = (Resolve-Path $SourceDb).Path
if (-not $OutputRoot) {
  $timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
  $OutputRoot = Join-Path $repoRoot ("reports/migration-rehearsal/{0}" -f $timestamp)
} elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
  $OutputRoot = Join-Path $repoRoot $OutputRoot
}

if ((Test-Path -LiteralPath $OutputRoot) -and (-not $Overwrite.IsPresent)) {
  throw "Output root already exists: $OutputRoot"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$sourceCopy = Join-Path $OutputRoot "source-legacy.db"
$targetDb = Join-Path $OutputRoot "migrated-current.db"
$summaryJson = Join-Path $OutputRoot "migration-summary.json"

Copy-Item -LiteralPath $resolvedSource -Destination $sourceCopy -Force

$command = @(
  "python",
  "scripts/migrate_legacy_platform_schema.py",
  "--source-db", $sourceCopy,
  "--target-db", $targetDb,
  "--summary-out", $summaryJson,
  "--overwrite"
)

& $command[0] $command[1] $command[2] $command[3] $command[4] $command[5] $command[6] $command[7]

Write-Host "Rehearsal complete."
Write-Host "Output root: $OutputRoot"
Write-Host "Migrated DB: $targetDb"
Write-Host "Summary JSON: $summaryJson"
