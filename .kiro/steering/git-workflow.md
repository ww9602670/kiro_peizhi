# Git 工作流规范（v4）

## 分支策略

### 主分支
- `main`：唯一的稳定分支，仅通过 PR 合并
- **禁止**直接 push 到 main（需在 GitHub 仓库设置中配置 Branch Protection）

### 开发分支命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/user-auth` |
| `fix/` | 修复 | `fix/health-endpoint-500` |
| `chore/` | 工程配置/依赖 | `chore/kiro-steering-git-workflow` |
| `docs/` | 文档 | `docs/commit-convention` |
| `refactor/` | 重构 | `refactor/request-layer` |
| `test/` | 测试 | `test/api-client-pbt` |

**规则**：
- 前缀使用英文（与 GitHub 生态一致）
- 分支名使用 kebab-case
- 从 `main` 创建分支，完成后通过 PR 合并回 `main`

## 中文 Commit 规范

**项目级硬规则**：所有 commit message 必须使用中文。

### 类型枚举（固定）

| 类型 | 含义 | 示例场景 |
|------|------|----------|
| 初始化 | 项目/模块初始化 | 项目脚手架、首次配置 |
| 配置 | 工程配置变更 | steering 文件、vite.config、pyproject.toml |
| 功能 | 新功能实现 | 新增 API 端点、新增页面 |
| 修复 | Bug 修复 | 修复 422 信封解析、修复 proxy 配置 |
| 重构 | 代码重构（不改变行为） | 提取公共函数、优化结构 |
| 测试 | 测试相关 | 新增单元测试、PBT 属性测试 |
| 文档 | 文档变更 | README、commit-convention、dev-workflow |
| 杂项 | 其他（依赖升级、CI 等） | 升级 pydantic、清理无用文件 |

### Commit Message 模板
```
<类型>：<简要描述>

<可选：详细说明>

<可选：关联 issue/spec>
```

### 示例
```
配置：新增 Git 工作流 steering 与 pre-commit hook

- 新增 git-workflow.md（always-on steering）
- 新增 scripts/setup-repo.ps1 一键初始化
- 新增 scripts/pre-commit-hook.ps1 接入 verify-dev.ps1 静态检查

关联：Kiro-Steering v4
```

```
功能：实现用户列表 API 端点

- 后端：backend/app/api/user.py + schemas/user.py
- 前端：frontend/src/api/user.ts + types/api/user.ts
- 成对提交（契约先行规范）
```

```
修复：修复 request.ts 对 422 响应的信封解析

isEnvelope 未检查 data 字段导致静默失败，
现已增加 'data' in obj 检查。
```

## 提交前验证（Pre-commit Hook）

### 机制
- 使用 Git 原生 hook（`.git/hooks/pre-commit`），不引入第三方工具
- hook 调用 `scripts/pre-commit-hook.ps1`
- 执行 verify-dev.ps1 的**静态检查子集**（不含 Proxy 链路验证，因提交时不一定启动了 dev server）

### 检查项

| # | 检查 | 来源 | 失败行为 |
|---|------|------|----------|
| 1 | Proxy-only 静态扫描 | verify-dev.ps1 B.6.1 | 阻断 commit |
| 2 | 契约漂移成对提交 | verify-dev.ps1 B.6.2 | 阻断 commit |

### 安装方式
- 运行 `pwsh scripts/setup-repo.ps1` 自动配置
- 或手动：将 `.git/hooks/pre-commit` 指向 `scripts/pre-commit-hook.ps1`

### 跳过 Hook（紧急情况）
- `git commit --no-verify` 可跳过 hook
- **仅限紧急修复**，事后必须补充验证

## PR 合并规则

### GitHub 仓库设置（手动操作）

在 GitHub 仓库 Settings → Branches → Branch protection rules 中配置：

1. Branch name pattern: `main`
2. ✅ Require a pull request before merging
3. ✅ Require approvals: 1（若为个人项目可设为 0，但仍必须走 PR）
4. ✅ Require status checks to pass before merging（可选：接入 CI 后启用）
5. ✅ Do not allow bypassing the above settings

### PR 流程

1. 从 `main` 创建功能分支
2. 开发 + 本地验证（pre-commit hook 自动执行）
3. push 到 origin
4. 创建 PR → 填写描述（中文）
5. 审查通过 → Squash and merge（保持 main 历史整洁）
6. 删除已合并的功能分支

### PR 描述模板（推荐）
```markdown
## 变更内容
<!-- 简要描述本次 PR 做了什么 -->

## 变更类型
- [ ] 功能
- [ ] 修复
- [ ] 配置
- [ ] 文档
- [ ] 重构
- [ ] 测试

## 验证方式
<!-- 如何验证这个变更是正确的 -->

## 关联
<!-- 关联的 spec / issue -->
```
