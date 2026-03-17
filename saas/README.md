# PC28 自动投注托管 SaaS 平台

## 项目结构

```
saas/
├── backend/          # Python FastAPI 后端
│   ├── app/          # 核心业务代码
│   │   ├── api/      # REST API 路由
│   │   ├── engine/   # 投注引擎（Worker、策略、结算等）
│   │   ├── models/   # 数据库操作层
│   │   ├── schemas/  # Pydantic 数据模型
│   │   └── utils/    # 工具函数
│   └── pyproject.toml
├── frontend/         # React + TypeScript 前端
│   ├── src/
│   │   ├── api/      # API 请求封装
│   │   ├── components/ # 通用组件
│   │   ├── hooks/    # React Hooks
│   │   ├── pages/    # 页面（admin / operator）
│   │   ├── types/    # TypeScript 类型定义
│   │   └── utils/    # 工具函数
│   └── package.json
└── README.md
```

## 环境要求

- Python >= 3.10
- Node.js >= 18
- pnpm

## 快速启动

### 后端

```bash
cd backend
pip install -e .
python -m uvicorn app.main:app --host 0.0.0.0 --port 8888
```

数据库（SQLite）会在首次启动时自动创建于 `backend/data/bocai.db`。

默认管理员账号：`admin` / `admin123`

### 前端

```bash
cd frontend
pnpm install
pnpm dev        # 开发模式（代理到 localhost:8888）
pnpm build      # 生产构建
```

## 技术栈

- 后端：FastAPI + aiosqlite + PyJWT + ddddocr
- 前端：React 19 + TypeScript + Vite
- 数据库：SQLite (WAL 模式)
