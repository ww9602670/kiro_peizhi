# 浏览器E2E测试 - 完整设置指南

## 概述

本指南将帮助您从零开始设置和运行浏览器E2E测试，包括：
- 停止旧的测试账号和策略
- 重启前后端服务
- 创建新的测试账号
- 运行完整的浏览器测试（支持mock和真实test166平台）

## 快速开始（推荐）

### 方式1：使用自动化脚本（最简单）

```powershell
cd backend
.\run_browser_e2e_complete.ps1
```

这个脚本会自动完成所有准备工作并运行测试。

### 方式2：手动执行（更多控制）

按照下面的详细步骤操作。

---

## 详细步骤

### 步骤1：停止旧的策略和清理数据

```powershell
cd backend
python prepare_browser_tests.py
```

这个脚本会：
- 停止所有运行中的策略
- 清除所有worker锁
- 创建或清理测试运营商账号 `e2e_browser_test`
- 删除该账号的所有旧数据（投注记录、对账记录、告警、策略、账号）

**输出示例：**
```
============================================================
准备浏览器E2E测试环境
============================================================

[步骤1] 停止所有运行中的策略...
  ✓ 已停止 2 个策略

[步骤2] 清除所有 worker 锁...
  ✓ 已清除 3 个账号的 worker 锁

[步骤3] 准备测试运营商...
  ✓ 使用现有运营商: e2e_browser_test (ID: 123)

[步骤4] 清理旧测试数据...
  ✓ 删除 50 条投注记录
  ✓ 删除 10 条对账记录
  ✓ 删除 5 条告警
  ✓ 删除 2 个策略
  ✓ 删除 1 个账号

============================================================
测试账号信息
============================================================

运营商账号: e2e_browser_test
运营商密码: test123456
运营商ID: 123
```

### 步骤2：重启后端服务

**终端1：启动后端**

```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

**验证后端运行：**
```powershell
# 在另一个终端
curl http://localhost:8888/api/v1/health
```

应该返回：
```json
{"status": "ok"}
```

### 步骤3：重启前端服务

**终端2：启动前端**

```powershell
cd frontend
pnpm dev
```

**验证前端运行：**

在浏览器打开 http://localhost:5173，应该看到登录页面。

### 步骤4：安装Playwright（如果未安装）

```powershell
cd backend

# 安装Python包
pip install playwright pytest-playwright

# 安装Chromium浏览器
playwright install chromium
```

### 步骤5：创建截图目录

```powershell
cd backend
mkdir screenshots
```

### 步骤6：运行浏览器E2E测试

**终端3：运行测试**

```powershell
cd backend

# 运行所有测试
pytest tests/e2e/browser/test_full_workflow.py -v -s

# 运行特定测试
pytest tests/e2e/browser/test_full_workflow.py::TestBrowserFullWorkflow::test_login_and_view_dashboard -v -s
```

---

## 测试用例说明

### 1. test_login_and_view_dashboard
- 测试登录功能
- 验证Dashboard数据显示（余额、盈亏）
- 截图：`full-workflow-01-login.png`, `full-workflow-02-dashboard.png`

### 2. test_navigate_to_accounts
- 测试导航到账号管理页面
- 验证页面元素显示
- 截图：`full-workflow-03-accounts.png`

### 3. test_navigate_to_strategies
- 测试导航到策略管理页面
- 验证页面元素显示
- 截图：`full-workflow-04-strategies.png`

### 4. test_dashboard_auto_refresh
- 测试Dashboard 30秒自动刷新
- 监听API调用
- 截图：`full-workflow-05-dashboard-after-refresh.png`

### 5. test_view_bet_orders
- 测试投注记录页面
- 验证订单表格显示
- 截图：`full-workflow-06-bet-orders.png`

### 6. test_logout
- 测试登出功能
- 验证返回登录页面
- 截图：`full-workflow-07-logout.png`

---

## 测试账号信息

### 运营商账号（用于登录前端）
- **用户名**: `e2e_browser_test`
- **密码**: `test123456`
- **角色**: operator

### 平台账号（用于绑定）

#### Mock平台（测试用）
- **平台类型**: `MOCK`
- **账号名**: 任意
- **密码**: 任意

#### 真实test166平台
- **平台类型**: `JND28WEB`
- **账号名**: `testuser01`
- **密码**: `test166`

---

## 测试流程

### 基础测试流程（无需绑定账号）

1. 登录系统
2. 查看Dashboard（无数据）
3. 导航到各个页面
4. 验证页面元素
5. 登出

### 完整测试流程（绑定账号后）

1. 登录系统
2. 绑定平台账号（mock或真实）
3. 创建策略
4. 启动策略
5. 观察自动投注
6. 验证余额变化
7. 查看投注记录
8. 验证结算
9. 停止策略
10. 登出

---

## 配置选项

### 修改浏览器模式

编辑 `backend/tests/e2e/browser/conftest.py`:

```python
# 无头模式（不显示浏览器窗口）
browser = await p.chromium.launch(
    headless=True,  # 改为True
    slow_mo=0,      # 改为0加快速度
)

# 有头模式（显示浏览器窗口，便于调试）
browser = await p.chromium.launch(
    headless=False,  # 改为False
    slow_mo=500,     # 每个操作延迟500ms
)
```

### 修改测试账号

编辑 `backend/prepare_browser_tests.py`:

```python
test_username = "e2e_browser_test"  # 修改用户名
```

编辑 `backend/tests/e2e/browser/test_full_workflow.py`:

```python
await login_page.login("e2e_browser_test", "test123456")  # 修改登录信息
```

---

## 查看测试结果

### 截图位置

所有测试截图保存在 `backend/screenshots/` 目录：

```
screenshots/
├── full-workflow-01-login.png
├── full-workflow-02-dashboard.png
├── full-workflow-03-accounts.png
├── full-workflow-04-strategies.png
├── full-workflow-05-dashboard-after-refresh.png
├── full-workflow-06-bet-orders.png
└── full-workflow-07-logout.png
```

### 测试输出

测试运行时会在控制台输出详细信息：

```
============================================================
浏览器E2E测试 - 登录并查看Dashboard
============================================================

[步骤1] 打开登录页面...
[步骤2] 登录系统...
[步骤3] 验证Dashboard显示...
  ✓ 总余额: 0.00
  ✓ 当日盈亏: 0.00
  ✓ 总盈亏: 0.00

✓ Dashboard显示验证通过
```

---

## 调试技巧

### 1. 暂停执行查看页面

在测试代码中添加：

```python
await page.pause()  # 打开Playwright Inspector
```

### 2. 查看浏览器控制台

```python
page.on("console", lambda msg: print(f"Console: {msg.text}"))
```

### 3. 慢速模式

在 `conftest.py` 中增加 `slow_mo` 值：

```python
slow_mo=1000,  # 每个操作延迟1秒
```

### 4. 截图调试

在任何位置添加截图：

```python
await page.screenshot(path="screenshots/debug.png")
```

### 5. 查看页面HTML

```python
html = await page.content()
print(html)
```

---

## 常见问题

### Q1: ModuleNotFoundError: No module named 'playwright'

**解决方案**:
```powershell
cd backend
pip install playwright pytest-playwright
playwright install chromium
```

### Q2: 测试超时

**原因**: 前端或后端服务未启动

**解决方案**:
1. 检查后端: `curl http://localhost:8888/api/v1/health`
2. 检查前端: 浏览器打开 `http://localhost:5173`

### Q3: 找不到元素

**原因**: 选择器不正确或页面加载慢

**解决方案**:
1. 使用 `await page.pause()` 查看页面结构
2. 增加等待时间: `await page.wait_for_selector('.element', timeout=10000)`
3. 使用更宽松的选择器: `text=按钮文本`

### Q4: 浏览器闪退

**原因**: Chromium未正确安装

**解决方案**:
```powershell
playwright install chromium --force
```

### Q5: 截图目录不存在

**解决方案**:
```powershell
cd backend
mkdir screenshots
```

### Q6: 登录失败

**原因**: 测试账号未创建或密码错误

**解决方案**:
```powershell
cd backend
python prepare_browser_tests.py
```

---

## 下一步

### 1. 基础测试（推荐先运行）

运行基础测试验证环境配置：

```powershell
pytest tests/e2e/browser/test_full_workflow.py -v -s
```

### 2. 绑定Mock平台测试

1. 登录前端 http://localhost:5173
2. 使用账号 `e2e_browser_test` / `test123456`
3. 绑定Mock平台账号
4. 创建策略并启动
5. 观察测试行为

### 3. 绑定真实test166平台测试

1. 登录前端
2. 绑定真实平台账号 `testuser01` / `test166`
3. 创建策略并启动
4. 观察真实投注和结算

### 4. 添加更多测试用例

参考现有测试用例，添加更多场景：
- 账号绑定流程
- 策略创建流程
- 策略启动/停止
- 投注观察
- 结算验证
- 余额一致性检查

---

## 参考资料

- [Playwright文档](https://playwright.dev/python/)
- [pytest-playwright文档](https://github.com/microsoft/playwright-pytest)
- [E2E测试最佳实践](../E2E_V2_REQUIREMENTS.md)
- [浏览器测试运行指南](./RUN_BROWSER_TESTS.md)

---

## 总结

使用本指南，您可以：

1. ✅ 快速准备测试环境
2. ✅ 运行完整的浏览器E2E测试
3. ✅ 测试mock和真实平台
4. ✅ 验证前端UI交互
5. ✅ 检查数据一致性
6. ✅ 调试测试问题

如有问题，请查看"常见问题"部分或查看测试输出和截图。
