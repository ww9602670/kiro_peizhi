#!/usr/bin/env pwsh

[CmdletBinding()]
param(
  [string]$OutputRoot,
  [switch]$IncludeFrontendDist,
  [switch]$IncludeBackendData
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not $OutputRoot) {
  $timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
  $OutputRoot = Join-Path $repoRoot ("release-candidates/{0}/root-app-linux" -f $timestamp)
} elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
  $OutputRoot = Join-Path $repoRoot $OutputRoot
}

if (Test-Path -LiteralPath $OutputRoot) {
  throw "Output path already exists: $OutputRoot"
}

$outputParent = Split-Path -Parent $OutputRoot
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null

$copiedFiles = New-Object System.Collections.Generic.List[string]
$excludedFiles = New-Object System.Collections.Generic.List[string]
$missingPaths = New-Object System.Collections.Generic.List[string]

function Ensure-ParentDirectory {
  param([string]$Path)
  $parent = Split-Path -Parent $Path
  if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
}

function Get-RepoRelativePath {
  param([string]$AbsolutePath)
  $fullPath = [System.IO.Path]::GetFullPath($AbsolutePath)
  $basePath = [System.IO.Path]::GetFullPath($repoRoot)
  if ($fullPath.StartsWith($basePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    return $fullPath.Substring($basePath.Length).TrimStart("\", "/").Replace("\", "/")
  }
  throw "Path is outside repo root: $AbsolutePath"
}

function Test-CommonExclusion {
  param([string]$RelativePath)

  $normalized = $RelativePath.Replace("\", "/")
  $segments = $normalized.Split("/")
  $fileName = [System.IO.Path]::GetFileName($normalized)

  foreach ($segment in $segments) {
    if ($segment -in @("__pycache__", ".pytest_cache", "node_modules", "screenshots")) {
      return $true
    }
  }

  if ($fileName -like "*.pyc" -or
      $fileName -like "*.pyo" -or
      $fileName -like "*.log" -or
      $fileName -like "*.out.log" -or
      $fileName -like "*.err.log") {
    return $true
  }

  return $false
}

function Copy-ExactFile {
  param([string]$RelativePath)

  $sourcePath = Join-Path $repoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $sourcePath)) {
    $missingPaths.Add($RelativePath)
    return
  }

  $destinationPath = Join-Path $OutputRoot $RelativePath
  Ensure-ParentDirectory -Path $destinationPath
  Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
  $copiedFiles.Add($RelativePath)
}

function Copy-Tree {
  param(
    [string]$RelativeRoot,
    [scriptblock]$IncludeFile
  )

  $sourceRoot = Join-Path $repoRoot $RelativeRoot
  if (-not (Test-Path -LiteralPath $sourceRoot)) {
    $missingPaths.Add($RelativeRoot)
    return
  }

  $files = Get-ChildItem -LiteralPath $sourceRoot -Recurse -File
  foreach ($file in $files) {
    $relativePath = Get-RepoRelativePath -AbsolutePath $file.FullName
    if (& $IncludeFile $relativePath) {
      $destinationPath = Join-Path $OutputRoot $relativePath
      Ensure-ParentDirectory -Path $destinationPath
      Copy-Item -LiteralPath $file.FullName -Destination $destinationPath -Force
      $copiedFiles.Add($relativePath)
    } else {
      $excludedFiles.Add($relativePath)
    }
  }
}

$exactFiles = @(
  "backend/pyproject.toml",
  "frontend/index.html",
  "frontend/package.json",
  "frontend/pnpm-lock.yaml",
  "frontend/tsconfig.json",
  "frontend/tsconfig.app.json",
  "frontend/tsconfig.node.json",
  "frontend/vite.config.ts",
  "frontend/.env.production",
  "scripts/migrate_legacy_platform_schema.py",
  "scripts/server_sqlite_backup.py"
)

foreach ($file in $exactFiles) {
  Copy-ExactFile -RelativePath $file
}

Copy-Tree -RelativeRoot "backend/app" -IncludeFile {
  param($relativePath)
  return -not (Test-CommonExclusion -RelativePath $relativePath)
}

Copy-Tree -RelativeRoot "frontend/src" -IncludeFile {
  param($relativePath)
  if (Test-CommonExclusion -RelativePath $relativePath) {
    return $false
  }
  $fileName = [System.IO.Path]::GetFileName($relativePath)
  if ($fileName -eq "test-setup.ts" -or $fileName -like "*.test.*") {
    return $false
  }
  return $true
}

Copy-Tree -RelativeRoot "frontend/public" -IncludeFile {
  param($relativePath)
  return -not (Test-CommonExclusion -RelativePath $relativePath)
}

Copy-Tree -RelativeRoot "deploy/linux" -IncludeFile {
  param($relativePath)
  if (Test-CommonExclusion -RelativePath $relativePath) {
    return $false
  }
  return $relativePath -ne "deploy/linux/backend.env"
}

if ($IncludeFrontendDist) {
  Copy-Tree -RelativeRoot "frontend/dist" -IncludeFile {
    param($relativePath)
    return -not (Test-CommonExclusion -RelativePath $relativePath)
  }
}

if ($IncludeBackendData) {
  Copy-Tree -RelativeRoot "backend/data" -IncludeFile {
    param($relativePath)
    return -not (Test-CommonExclusion -RelativePath $relativePath)
  }
}

$metadataPath = Join-Path $OutputRoot "RELEASE-CANDIDATE.md"
$manifestPath = Join-Path $OutputRoot "included-files.txt"
$excludedPath = Join-Path $OutputRoot "excluded-files.txt"
$missingPath = Join-Path $OutputRoot "missing-paths.txt"

$head = git rev-parse HEAD
$branch = git branch --show-current
$createdAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"

$metadata = @"
# Linux Release Candidate

Created at $createdAt.

## Source

- repo root: $repoRoot
- branch: $branch
- head: $head

## Release scope

- current production line: root backend + root frontend
- includes saas: no
- includes frontend dist: $($IncludeFrontendDist.IsPresent.ToString().ToLowerInvariant())
- includes backend data: $($IncludeBackendData.IsPresent.ToString().ToLowerInvariant())

## Copied file count

- copied files: $($copiedFiles.Count)
- excluded files: $($excludedFiles.Count)
- missing optional paths: $($missingPaths.Count)

## Included roots

- backend/app/**
- backend/pyproject.toml
- frontend/src/** excluding tests
- frontend/public/**
- frontend/index.html
- frontend/package.json
- frontend/pnpm-lock.yaml
- frontend/tsconfig*.json
- frontend/vite.config.ts
- scripts/migrate_legacy_platform_schema.py
- scripts/server_sqlite_backup.py
- frontend/.env.production
- deploy/linux/**

## Notes

- This is a source-oriented release candidate, not a deployed runtime.
- Runtime artifacts, probes, reports, saas, and local-only files are intentionally excluded.
"@

Set-Content -LiteralPath $metadataPath -Value $metadata -Encoding UTF8
$copiedFiles | Sort-Object | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$excludedFiles | Sort-Object | Set-Content -LiteralPath $excludedPath -Encoding UTF8
$missingPaths | Sort-Object | Set-Content -LiteralPath $missingPath -Encoding UTF8

[pscustomobject]@{
  output_root = $OutputRoot
  copied_files = $copiedFiles.Count
  excluded_files = $excludedFiles.Count
  missing_paths = $missingPaths.Count
  include_frontend_dist = $IncludeFrontendDist.IsPresent
  include_backend_data = $IncludeBackendData.IsPresent
} | ConvertTo-Json -Compress
