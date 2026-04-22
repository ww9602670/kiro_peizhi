Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Write-Section {
  param([string]$Message)
  Write-Host ("==> {0}" -f $Message)
}

function Resolve-FirstCommand {
  param([string[]]$Names)

  foreach ($name in $Names) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) {
      if ($command.Path) {
        return $command.Path
      }
      return $command.Name
    }
  }

  throw "Required command not found. Tried: $($Names -join ', ')"
}

function Invoke-Process {
  param(
    [string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$WorkingDirectory,
    [switch]$DryRun
  )

  $display = $FilePath
  if ($ArgumentList.Count -gt 0) {
    $display = "{0} {1}" -f $FilePath, ($ArgumentList -join " ")
  }

  Write-Host (">> {0}" -f $display)
  if ($DryRun) {
    return @()
  }

  if ($WorkingDirectory) {
    Push-Location $WorkingDirectory
  }

  try {
    $output = & $FilePath @ArgumentList 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      if ($output) {
        $output | Write-Host
      }
      throw "Command failed with exit code ${exitCode}: $display"
    }
    return $output
  } finally {
    if ($WorkingDirectory) {
      Pop-Location
    }
  }
}

function Invoke-RemoteBash {
  param(
    [string]$ServerHost,
    [string]$Script,
    [switch]$DryRun
  )

  Write-Host (">> ssh {0} bash -s" -f $ServerHost)
  if ($DryRun) {
    return @()
  }

  $output = $Script | & ssh $ServerHost "bash -s" 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    if ($output) {
      $output | Write-Host
    }
    throw "Remote script failed with exit code $exitCode on $ServerHost"
  }
  return $output
}

function Invoke-ScpUpload {
  param(
    [string]$LocalPath,
    [string]$ServerHost,
    [string]$RemotePath,
    [switch]$DryRun
  )

  Write-Host (">> scp {0} {1}:{2}" -f $LocalPath, $ServerHost, $RemotePath)
  if ($DryRun) {
    return
  }

  & scp $LocalPath ("{0}:{1}" -f $ServerHost, $RemotePath)
  if ($LASTEXITCODE -ne 0) {
    throw "scp upload failed: ${LocalPath} -> ${ServerHost}:$RemotePath"
  }
}

function Parse-KeyValueOutput {
  param([object[]]$Lines)

  $result = @{}
  foreach ($line in $Lines) {
    $text = [string]$line
    if ($text -match "^(?<key>[A-Z0-9_]+)=(?<value>.*)$") {
      $result[$matches["key"]] = $matches["value"]
    }
  }
  return $result
}

function Get-GitCurrentBranch {
  $branch = git branch --show-current
  if ($LASTEXITCODE -ne 0 -or -not $branch) {
    throw "Unable to determine current Git branch."
  }
  return [string]$branch
}

function Get-GitCommit {
  param([switch]$Short)

  $args = @("rev-parse")
  if ($Short) {
    $args += "--short"
  }
  $args += "HEAD"

  $commit = git @args
  if ($LASTEXITCODE -ne 0 -or -not $commit) {
    throw "Unable to determine Git commit."
  }
  return [string]$commit
}

function Assert-GitBranch {
  param([string]$ExpectedBranch)

  $branch = Get-GitCurrentBranch
  if ($branch -ne $ExpectedBranch) {
    throw "Current branch is '$branch'. Expected '$ExpectedBranch'."
  }
}

function Assert-GitClean {
  $status = git status --short
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to read Git working tree status."
  }
  if ($status) {
    throw "Working tree is not clean. Commit or stash changes before release/rollback."
  }
}

function New-ReleaseTimestamp {
  return Get-Date -Format "yyyyMMdd_HHmmss"
}

function Test-GitCommitExists {
  param([string]$Commit)

  git rev-parse --verify ("{0}^{{commit}}" -f $Commit) *> $null
  return ($LASTEXITCODE -eq 0)
}

function Ensure-GitCommitAvailable {
  param(
    [string]$Commit,
    [string]$RemoteName,
    [switch]$DryRun
  )

  if ($DryRun) {
    return
  }

  if (-not (Test-GitCommitExists -Commit $Commit)) {
    Invoke-Process -FilePath "git" -ArgumentList @("fetch", $RemoteName, "--prune", "--tags")
    if (-not (Test-GitCommitExists -Commit $Commit)) {
      throw "Commit not available locally after fetch: $Commit"
    }
  }
}

function Update-LiveMirrorBranch {
  param(
    [string]$RemoteName,
    [string]$LiveBranch,
    [string]$Commit,
    [switch]$DryRun
  )

  Ensure-GitCommitAvailable -Commit $Commit -RemoteName $RemoteName -DryRun:$DryRun
  Invoke-Process -FilePath "git" -ArgumentList @("branch", "-f", $LiveBranch, $Commit) -DryRun:$DryRun
  Invoke-Process -FilePath "git" -ArgumentList @("push", "--force-with-lease", $RemoteName, ("{0}:refs/heads/{1}" -f $Commit, $LiveBranch)) -DryRun:$DryRun
}
