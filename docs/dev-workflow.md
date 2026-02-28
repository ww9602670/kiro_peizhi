# 开发工作流 Runbook

## 一、仓库初始化（一次性）

### 1.1 创建 GitHub 仓库
1. 在 GitHub 上创建空仓库（不勾选 README / .gitignore / LICENSE）
2. 记录仓库地址：`https://github.com/<owner>/<repo>.git`

### 1.2 本地初始化
```powershell
git init
git remote add origin https://github.com/<owner>/<repo>.git
pwsh scripts/setup-repo.ps1
```

### 1.3 配置 main 分支保护（GitHub UI 手动操作）

| 设置项 | 值 |
|--------|-----|
| Branch name pattern | `main` |
| Require a pull request before merging | ✅ |
| Required approvals | 1（个人项目可设 0，但仍走 PR） |
| Require status checks to pass | ✅（接入 CI 后启用） |
| Do not allow bypassing | ✅ |

### 1.4 用户级 Git 配置（手动执行，不入 git）
```powershell
git config --global user.name "[你的名字]"
git config --global user.email "[你的邮箱]"
# 可选：SSH key 参考 https://docs.github.com/en/authentication/connecting-to-github-with-ssh
```

## 二、日常开发流程

### 2.1 创建功能分支
```powershell
git checkout main
git pull origin main
git checkout -b feat/my-feature
```

### 2.2 开发 + 提交
```powershell
git add .
git commit -m "功能：实现 xxx 功能"
# pre-commit hook 自动执行静态检查
```

### 2.3 Push + 创建 PR
```powershell
git push origin feat/my-feature
```
在 GitHub 上创建 PR → 审查通过 → Squash and merge → 删除分支

## 三、紧急修复流程
```powershell
git checkout -b fix/urgent-issue
git commit -m "修复：紧急修复 xxx"
# 极端情况：git commit --no-verify -m "修复：紧急修复（跳过 hook，事后补验证）"
git push origin fix/urgent-issue
```

## 四、验证命令速查

| 场景 | 命令 |
|------|------|
| 完整联调自检 | `pwsh scripts/verify-dev.ps1` |
| 仅静态检查 | `pwsh scripts/pre-commit-hook.ps1` |
| 检查 hook 是否安装 | `Get-Content .git/hooks/pre-commit` |
| 重新安装 hook | `pwsh scripts/setup-repo.ps1` |
