# 中文 Commit 规范

> 本文档是 `.kiro/steering/git-workflow.md` 的详细补充。

## 为什么用中文 Commit
- 团队主要使用中文沟通，中文 commit 降低认知切换成本
- Git log 直接可读，无需翻译
- 分支名保持英文前缀（与 GitHub 生态兼容）

## 类型枚举

| 类型 | 含义 | 英文对照（仅参考） |
|------|------|---------------------|
| 初始化 | 项目/模块初始化 | init |
| 配置 | 工程配置变更 | config / chore |
| 功能 | 新功能实现 | feat |
| 修复 | Bug 修复 | fix |
| 重构 | 代码重构 | refactor |
| 测试 | 测试相关 | test |
| 文档 | 文档变更 | docs |
| 杂项 | 其他 | chore |

## 格式
```
<类型>：<简要描述>

<可选正文>

<可选脚注>
```

**注意**：类型与描述之间使用中文全角冒号 `：`

## 示例

### 最小 commit
```
配置：更新 vite proxy 端口为 8888
```

### 带正文
```
修复：修复 isEnvelope 未检查 data 字段

后端返回 {code:0, message:"success"} 无 data 时，
旧版 isEnvelope 通过检查但 r.data 为 undefined，
导致前端静默失败。现增加 'data' in obj 检查。
```

### 带关联
```
功能：实现 health 端到端联调路径

- 后端 /api/v1/health 端点
- 前端 fetchHealth() 通过统一请求层调用
- Proxy 链路验证通过

关联：Kiro-Steering v4 Gate0
```

## 不合规示例
```
❌ feat: add user api          （英文 + 英文类型）
❌ 新增用户接口                  （缺少类型前缀）
❌ 功能:新增用户接口             （半角冒号 + 无空格）
❌ 功能： 新增用户接口           （冒号后多余空格）
```
