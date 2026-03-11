# 技术栈

## 前端
- 框架：React + TypeScript
- 构建工具：Vite
- 包管理：pnpm
- 开发端口：5173（Vite 默认）
- 路径别名：`@/` → `src/`（tsconfig paths + vite resolve.alias）

## 后端
- 框架：FastAPI（Python）
- 数据验证：Pydantic v2（>= 2.0）
- 依赖管理：pyproject.toml（不使用 requirements.txt）
- 开发端口：8888
- 启动命令：`uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload`

## 开发模式
- 网络模式：Proxy-Only（唯一模式，详见 dev-loop.md）
- API 基础路径：`/api/v1`（相对路径）
- 前端代理：Vite proxy `/api` → `http://localhost:8888`

## 测试
- 前端：Vitest + React Testing Library
- 后端：pytest
- 属性测试：fast-check（前端）/ hypothesis（后端）
