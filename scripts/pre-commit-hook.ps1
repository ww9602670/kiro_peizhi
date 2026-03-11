#!/usr/bin/env pwsh
# scripts/pre-commit-hook.ps1 — Git pre-commit 静态检查（v4）
# 由 .git/hooks/pre-commit 调用
# 仅执行静态检查（不含 Proxy 链路验证）

$ErrorActionPreference = "Stop"
Write-Host "=== Pre-commit 检查 ===" -ForegroundColor Cyan

# --- 1. Proxy-only 静态扫描 ---
Write-Host "[1/2] Proxy-only 静态扫描..." -ForegroundColor Yellow
$hits = rg "https?://[^`"']+?/api/" --glob "!node_modules" --glob "!dist" --glob "!*.md" --glob "!scripts/*" --glob "!docs/*" -l 2>$null
if ($hits) {
  Write-Host "❌ 发现硬编码绝对 API URL（违反 Proxy-only 规则）：" -ForegroundColor Red
  $hits | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  Write-Host "`n提交被阻断。请修复后重新提交。" -ForegroundColor Red
  exit 1
}
Write-Host "✅ 通过" -ForegroundColor Green

# --- 2. 契约漂移成对提交检查 ---
Write-Host "[2/2] 契约漂移成对提交检查..." -ForegroundColor Yellow
$staged = git diff --cached --name-only 2>$null

$backendSchemaChanged = $staged | Where-Object { $_ -match "^backend/app/schemas/" }
$frontendTypesChanged = $staged | Where-Object { $_ -match "^frontend/src/types/" }

if ($backendSchemaChanged -and -not $frontendTypesChanged) {
  Write-Host "❌ 契约漂移：backend/app/schemas/ 有变更但 frontend/src/types/ 无变更" -ForegroundColor Red
  $backendSchemaChanged | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  Write-Host "`n提交被阻断。请同步更新前端类型后重新提交。" -ForegroundColor Red
  Write-Host "（紧急情况可用 git commit --no-verify 跳过，事后必须补充）" -ForegroundColor Yellow
  exit 1
}

if ($frontendTypesChanged -and -not $backendSchemaChanged) {
  Write-Host "⚠️ 注意：frontend/src/types/ 有变更但 backend/app/schemas/ 无变更（可能是前端独立类型，非违规但请确认）" -ForegroundColor Yellow
}

Write-Host "✅ 通过" -ForegroundColor Green
Write-Host "`n=== Pre-commit 检查全部通过 ===" -ForegroundColor Cyan
