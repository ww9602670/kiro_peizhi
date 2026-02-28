# 目录结构与命名规范

## 项目根目录结构

```
项目根/
├── frontend/                    # 前端（React + TypeScript + Vite）
│   ├── src/
│   │   ├── api/                 # API 调用封装（统一请求层）
│   │   │   └── request.ts       # 统一请求层入口
│   │   ├── components/          # 通用组件
│   │   ├── pages/               # 页面组件
│   │   ├── types/               # TypeScript 类型定义
│   │   │   └── api/             # API 契约类型（与后端 schema 对应）
│   │   ├── hooks/               # 自定义 hooks
│   │   ├── utils/               # 工具函数
│   │   └── App.tsx
│   ├── tests/
│   │   ├── unit/                # 单元测试
│   │   └── e2e/                 # 端到端测试
│   ├── .env.development         # 开发环境变量
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── package.json
├── backend/                     # 后端（FastAPI + Python）
│   ├── app/
│   │   ├── api/                 # API 路由（router 内只定义相对路径）
│   │   ├── schemas/             # Pydantic v2 Schema（契约定义）
│   │   ├── models/              # 数据库模型（如需要）
│   │   ├── utils/               # 工具函数
│   │   │   └── mock_helper.py   # Mock 数据 helper
│   │   └── main.py              # FastAPI 入口（统一 prefix）
│   ├── tests/                   # 后端测试
│   └── pyproject.toml           # Python 依赖管理
├── scripts/                     # 工程脚本
│   ├── verify-dev.ps1           # 一键联调自检
│   ├── setup-repo.ps1           # 一键初始化仓库 + hook
│   └── pre-commit-hook.ps1      # pre-commit 静态检查
├── docs/                        # 项目文档
│   ├── commit-convention.md     # 中文 commit 规范
│   └── dev-workflow.md          # 开发工作流 runbook
├── .kiro/                       # Kiro 配置
│   ├── steering/                # Steering 文件（19 个）
│   ├── settings/                # MCP 等设置
│   └── specs/                   # Feature specs
└── .gitignore
```

## 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 文件/目录 | kebab-case | `user-auth.ts`、`api-client.md` |
| React 组件文件 | PascalCase | `UserList.tsx`、`HealthStatus.tsx` |
| TypeScript 接口 | PascalCase | `UserResponse`、`ApiError` |
| Python 模块 | snake_case | `mock_helper.py`、`health.py` |
| Pydantic Schema | PascalCase | `UserCreate`、`UserResponse` |
| API 路径 | kebab-case | `/api/v1/user-list` |
| 分支名 | kebab-case + 前缀 | `feat/user-auth`、`fix/proxy-config` |

## 路径别名

前端使用 `@/` 作为 `src/` 的别名，需同时配置：

1. `tsconfig.json`：
```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  }
}
```

2. `vite.config.ts`：
```typescript
resolve: {
  alias: { '@': resolve(__dirname, 'src') }
}
```

**两处必须同时配置**，缺一不可。
