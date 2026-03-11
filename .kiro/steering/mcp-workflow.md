# MCP 工作流与联调闭环

## MCP 工具角色

| 工具 | 角色 | autoApprove |
|------|------|-------------|
| `navigate_page` | 导航到目标页面 | ✅ 是（低风险） |
| `take_screenshot` | 截图取证 | ✅ 是（低风险） |
| `take_snapshot` | 页面快照 | ✅ 是（低风险） |
| `evaluate_script` | 注入 Probe / 检测脚本 | ✅ 是（低风险 + 脚本审计约束） |
| `click` | 点击交互 | ❌ 否（需确认） |
| `fill` | 表单填写 | ❌ 否（需确认） |

## autoApprove 审计约束
- autoApprove 中的工具属于"低风险 + 脚本审计约束"类别
- `evaluate_script` 注入的脚本必须为只读采集，不得包含写操作（DOM 修改、网络请求等）
- 所有 autoApprove 工具的使用记录应可追溯

## Chrome Probe 注入

### Probe 注入脚本（evaluate_script）
```javascript
// 注入到页面，采集 API 请求信息
(() => {
  if (window.__kiProbe) return;
  window.__kiProbe = { requests: [], errors: [], maxEntries: 50 };
  const p = window.__kiProbe;

  const origFetch = window.fetch;
  window.fetch = async (...args) => {
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
    const entry = { url, method: args[1]?.method || 'GET', ts: Date.now() };
    try {
      const res = await origFetch(...args);
      const clone = res.clone();
      try { entry.body = await clone.json(); } catch { entry.body = null; }
      entry.status = res.status;
    } catch (e) {
      entry.error = e.message;
    }
    if (p.requests.length >= p.maxEntries) p.requests.shift();
    p.requests.push(entry);
    return origFetch(...args);
  };

  window.addEventListener('error', (e) => {
    if (p.errors.length >= p.maxEntries) p.errors.shift();
    p.errors.push({ message: e.message, ts: Date.now() });
  });
})()
```

### Probe 读取脚本（基础版）
```javascript
(() => {
  const p = window.__kiProbe;
  if (!p) return { probeMissing: true };
  return {
    probeMissing: false,
    requests: p.requests,
    errors: p.errors,
    directBackendHitDetected: p.requests.some(r =>
      /localhost:8888|127\.0\.0\.1:8888/.test(r.url)
    ),
    apiAbsoluteUrlHitDetected: p.requests.some(r =>
      /^https?:\/\/[^/]+\/api\//.test(r.url)
    )
  };
})()
```

### 直连后端检测
- Probe 读取结果中 `directBackendHitDetected` 必须为 `false`
- 如果为 `true`，说明有请求绕过了 Vite proxy 直连后端，属于违规

### API 绝对 URL 检测（v3.2.4）
- 代码中禁止硬编码绝对 API URL（如 `http://localhost:8888/api/v1/...`）
- 静态扫描：`rg "https?://[^\"']+?/api/" --glob "!node_modules" --glob "!dist" --glob "!*.md" --glob "!scripts/*" --glob "!docs/*"`
- 发现违规应立即修复，不得添加 CORS 配置来"解决"

## 联调验证流程（完整版）

> 详细步骤见上方"Chrome Smoke Test 标准流程"。以下为简要 checklist：

1. 启动后端 + 前端
2. `navigate_page` 到 `http://localhost:5173`
3. `take_snapshot` 确认页面结构
4. `evaluate_script` 注入 Probe
5. `navigate_page` reload（确保 Probe 覆盖首屏请求）
6. 等待页面加载完成
7. `evaluate_script` 读取 Probe 数据
8. 断言：
   - `probeMissing: false`
   - `requests` 非空（至少 1 条 API 请求）
   - `directBackendHitDetected: false`
   - `apiAbsoluteUrlHitDetected: false`
   - `errors` 为空或仅含非关键错误
9. `take_screenshot` 截图存档

## Specs Gate（v3.2.6）
- Specs 审核使用 gpt-5.2（codex-gpt52）
- gpt-5.2 / gpt-5.3 是**模型角色策略**，不是项目 MCP 工具
- 它们的 MCP 配置在用户级（`~/.kiro/settings/mcp.json`），不入 git

## Chrome Smoke Test 标准流程（v3.2.7）

完整的 Chrome 联调验证应按以下顺序执行：

1. `navigate_page` → 目标页面（`http://localhost:5173`）
2. `take_snapshot` → 确认页面结构正常加载
3. `evaluate_script` → 注入 Probe（采集 fetch 请求 + 错误）
4. `navigate_page` → reload（确保 Probe 覆盖首屏请求）
5. 等待页面加载完成
6. `evaluate_script` → 读取 Probe 数据
7. 断言验证（见下方）
8. `take_screenshot` → 截图存档

### Probe 断言清单

| # | 断言项 | 预期值 | 失败含义 |
|---|--------|--------|----------|
| 1 | `probeMissing` | `false` | Probe 未注入或被页面刷新清除 |
| 2 | `requests` 数组 | 非空（至少 1 条） | 页面未发起任何 API 请求 |
| 3 | `directBackendHitDetected` | `false` | 有请求绕过 Vite proxy 直连后端 8888 |
| 4 | `apiAbsoluteUrlHitDetected`* | `false` | 有请求使用了绝对 URL（如 `http://localhost:8888/api/...`） |
| 5 | `errors` 数组 | 空或仅含非关键错误 | 页面存在 JS 运行时错误 |

> *`apiAbsoluteUrlHitDetected` 需在 Probe 读取脚本中扩展（见下方增强版）。

### 增强版 Probe 读取脚本（含 apiAbsoluteUrlHitDetected）
```javascript
(() => {
  const p = window.__kiProbe;
  if (!p) return { probeMissing: true };
  return {
    probeMissing: false,
    requests: p.requests,
    errors: p.errors,
    directBackendHitDetected: p.requests.some(r =>
      /localhost:8888|127\.0\.0\.1:8888/.test(r.url)
    ),
    apiAbsoluteUrlHitDetected: p.requests.some(r =>
      /^https?:\/\/[^/]+\/api\//.test(r.url)
    )
  };
})()
```

## evaluate_script 审计规则（v3.2.7）

### 允许的脚本行为（只读采集）
- 读取 DOM 元素文本/属性
- 读取 `window.__kiProbe` 数据
- 读取 `performance.getEntries()`
- 读取 `document.title` / `document.URL`

### 禁止的脚本行为（写操作）
- 修改 DOM（`innerHTML`、`appendChild`、`removeChild`）
- 发起网络请求（`fetch`、`XMLHttpRequest`、`navigator.sendBeacon`）
- 修改 `localStorage` / `sessionStorage` / `cookie`
- 重定向页面（`location.href = ...`）

### 审计方式
- 代码审查：每个 evaluate_script 调用的脚本源码必须可追溯
- Probe 注入脚本是唯一允许修改 `window.fetch` 的例外（用于拦截采集，不修改请求/响应）

## MCP 配置字段白名单
仅允许以下字段：`command` / `args` / `env` / `disabled` / `autoApprove`
- **禁止**添加任何白名单外的字段

## includeMcpJson 决策点（#24）

| 项目 | 说明 |
|------|------|
| 字段 | `includeMcpJson` |
| 默认 | **不启用**（不写入任何配置文件） |
| 原因 | 该字段未经 Kiro 官方明确证实；启用可能导致团队环境不一致（用户级 server 被意外纳入项目上下文） |
| 状态 | **已确定：禁止使用** |
| 备选 | 若未来 Kiro 官方文档明确支持，可重新评估 |

### 验证步骤（Verification Pack 联动）
1. 检查 `.kiro/settings/mcp.json` 不含 `includeMcpJson` 字段
2. 在 MCP Server 面板确认：仅项目级配置的 server（chrome-devtools）显示为 connected
3. 用户级 server（codex-gpt52 / codex-gpt53）不应出现在项目 MCP 面板中（除非用户级配置了）
4. 若项目级设置 `"disabled": true`，确认该 server 在面板中显示为 disabled 且不可调用
