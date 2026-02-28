#!/usr/bin/env pwsh
# scripts/setup-repo.ps1 — 一键初始化仓库 + 配置 Git hook（v4）
# 用法：在项目根目录执行 pwsh scripts/setup-repo.ps1

$ErrorActionPreference = "Stop"
Write-Host "=== 仓库初始化开始 ===" -ForegroundColor Cyan

# --- 1. 检查 git 仓库 ---
if (-not (Test-Path ".git")) {
  Write-Host "初始化 git 仓库..." -ForegroundColor Yellow
  git init
}

# --- 2. 配置 pre-commit hook ---
Write-Host "`n配置 pre-commit hook..." -ForegroundColor Yellow
$hookDir = ".git/hooks"
$hookFile = "$hookDir/pre-commit"

if (-not (Test-Path $hookDir)) {
  New-Item -ItemType Directory -Path $hookDir -Force | Out-Null
}

$hookContent = @'
#!/bin/sh
# Git pre-commit hook — 调用 PowerShell 静态检查
# 由 scripts/setup-repo.ps1 自动生成
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/pre-commit-hook.ps1
exit $?
'@

Set-Content -Path $hookFile -Value $hookContent -Encoding UTF8
Write-Host "✅ pre-commit hook 已配置：$hookFile" -ForegroundColor Green

# --- 3. 创建 .gitignore（如果不存在） ---
if (-not (Test-Path ".gitignore")) {
  Write-Host "`n创建 .gitignore..." -ForegroundColor Yellow
  $gitignore = @"
node_modules/
__pycache__/
*.pyc
.venv/
venv/
dist/
build/
*.egg-info/
.vscode/
.idea/
.env
.env.local
.env.*.local
!.env.development
.DS_Store
Thumbs.db
"@
  Set-Content -Path ".gitignore" -Value $gitignore -Encoding UTF8
  Write-Host "✅ .gitignore 已创建" -ForegroundColor Green
} else {
  Write-Host ".gitignore 已存在，跳过" -ForegroundColor Gray
}

# --- 4. 首次提交（如果没有任何 commit） ---
$hasCommits = git rev-parse HEAD 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "`n执行首次提交..." -ForegroundColor Yellow
  git add .
  git commit --no-verify -m "初始化：项目初始化 + Kiro Steering 配置 + Git 工作流"
  Write-Host "✅ 首次提交完成" -ForegroundColor Green
} else {
  Write-Host "`n已有 commit 历史，跳过首次提交" -ForegroundColor Gray
}

Write-Host "`n=== 仓库初始化完成 ===" -ForegroundColor Cyan
Write-Host @"

下一步：
1. 在 GitHub 创建空仓库
2. git remote add origin https://github.com/<owner>/<repo>.git
3. git push -u origin main
4. 在 GitHub Settings → Branches 配置 main 分支保护规则
   （详见 docs/dev-workflow.md）
"@ -ForegroundColor White
