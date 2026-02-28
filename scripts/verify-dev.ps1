#!/usr/bin/env pwsh
# scripts/verify-dev.ps1 — 一键联调自检脚本（v3.2.8 + v4）
# 用法：在项目根目录执行 pwsh scripts/verify-dev.ps1

$ErrorActionPreference = "Continue"
$pass = 0
$fail = 0
$warn = 0

Write-Host "=== 联调自检开始 ===" -ForegroundColor Cyan
Write-Host ""

# --- B.6.1 Proxy-only 静态扫描 ---
Write-Host "[B.6.1] Proxy-only 静态扫描" -ForegroundColor Yellow
$hits = rg "https?://[^`"']+?/api/" --glob "!node_modules" --glob "!dist" --glob "!*.md" --glob "!scripts/*" --glob "!docs/*" -l 2>$null
if ($hits) {
  Write-Host "  ❌ FAIL: 发现硬编码绝对 API URL：" -ForegroundColor Red
  $hits | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
  $fail++
} else {
  Write-Host "  ✅ PASS: 未发现硬编码绝对 API URL" -ForegroundColor Green
  $pass++
}

# --- B.6.2 契约漂移成对提交检查 ---
Write-Host "`n[B.6.2] 契约漂移成对提交检查" -ForegroundColor Yellow
$hasGit = Test-Path ".git"
if ($hasGit) {
  $staged = git diff --cached --name-only 2>$null
  $backendSchemaChanged = $staged | Where-Object { $_ -match "^backend/app/schemas/" }
  $frontendTypesChanged = $staged | Where-Object { $_ -match "^frontend/src/types/" }

  if ($backendSchemaChanged -and -not $frontendTypesChanged) {
    Write-Host "  ❌ FAIL: backend/app/schemas/ 有变更但 frontend/src/types/ 无变更" -ForegroundColor Red
    $fail++
  } else {
    Write-Host "  ✅ PASS: 契约成对提交检查通过" -ForegroundColor Green
    $pass++
  }
} else {
  Write-Host "  ⚠️ WARN: 非 git 仓库，跳过契约漂移检查" -ForegroundColor Yellow
  $warn++
}

# --- B.6.3 Proxy 链路双向验证 ---
Write-Host "`n[B.6.3] Proxy 链路双向验证" -ForegroundColor Yellow
try {
  $backendRes = Invoke-RestMethod -Uri "http://localhost:8888/api/v1/health" -TimeoutSec 5 -ErrorAction Stop
  if ($backendRes.code -eq 0) {
    Write-Host "  ✅ PASS: 后端直连 http://localhost:8888/api/v1/health 返回 code=0" -ForegroundColor Green
    $pass++
  } else {
    Write-Host "  ❌ FAIL: 后端返回 code=$($backendRes.code)" -ForegroundColor Red
    $fail++
  }
} catch {
  Write-Host "  ⚠️ WARN: 后端未启动或不可达（跳过链路验证）" -ForegroundColor Yellow
  $warn++
}

try {
  $proxyRes = Invoke-RestMethod -Uri "http://localhost:5173/api/v1/health" -TimeoutSec 5 -ErrorAction Stop
  if ($proxyRes.code -eq 0) {
    Write-Host "  ✅ PASS: Proxy 链路 http://localhost:5173/api/v1/health 返回 code=0" -ForegroundColor Green
    $pass++
  } else {
    Write-Host "  ❌ FAIL: Proxy 链路返回 code=$($proxyRes.code)" -ForegroundColor Red
    $fail++
  }
} catch {
  Write-Host "  ⚠️ WARN: 前端未启动或 Proxy 不可达（跳过链路验证）" -ForegroundColor Yellow
  $warn++
}

# --- B.7 Git Hook 检查（v4 新增） ---
Write-Host "`n[B.7] Git Hook 检查" -ForegroundColor Yellow
if (Test-Path ".git/hooks/pre-commit") {
  $hookContent = Get-Content ".git/hooks/pre-commit" -Raw
  if ($hookContent -match "pre-commit-hook.ps1") {
    Write-Host "  ✅ PASS: pre-commit hook 已配置且引用 pre-commit-hook.ps1" -ForegroundColor Green
    $pass++
  } else {
    Write-Host "  ⚠️ WARN: pre-commit hook 存在但未引用 pre-commit-hook.ps1" -ForegroundColor Yellow
    $warn++
  }
} else {
  Write-Host "  ❌ FAIL: .git/hooks/pre-commit 不存在（运行 pwsh scripts/setup-repo.ps1 安装）" -ForegroundColor Red
  $fail++
}

# --- 汇总 ---
Write-Host "`n=== 联调自检完成 ===" -ForegroundColor Cyan
Write-Host "  PASS: $pass | FAIL: $fail | WARN: $warn" -ForegroundColor $(if ($fail -gt 0) { "Red" } else { "Green" })

if ($fail -gt 0) {
  Write-Host "`n请修复上述 FAIL 项后重新运行。" -ForegroundColor Red
  exit 1
} else {
  Write-Host "`n所有必检项通过。" -ForegroundColor Green
  exit 0
}
