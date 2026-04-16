# 运行浏览器E2E测试指南

## 前置条件

### 1. 安装Playwright

```powershell
# 在backend目录下安装
cd backend
pip install playwright pytest-playwright

# 安装浏览器驱动（Chromium）
playwright install chromium
```

### 2. 创建截图目录

```powershell
# 在backend目录下创建
mkdir screenshots
```

## 启动服务

浏览器E2E测试需要同时运行前端和后端服务。

### 终端1：启动后端服务

```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

验证后端运行：
```powershell
curl http://localhost:8888/api/v1/health
```

### 终端2：启动前端服务

```powershell
cd frontend
pnpm dev
```

前端默认运行在 http://localhost:5173

验证前端运行：在浏览器打开 http://localhost:5173

## 运行测试

### 终端3：运行浏览器E2E测试

```powershell
cd backend

# 运行所有浏览器E2E测试
pytest tests/e2e/browser/ -v -s

# 运行特定测试文件
pytest tests/e2e/browser/test_full_workflow.py -v -s

# 运行特定测试用例
pytest tests/e2e/browser/test_full_workflow.py::TestBrowserFullWorkflow::test_login_and_view_dashboard -v -s
```

## 测试用例说明

### test_full_workflow.py

1. **test_login_and_view_dashboard** - 登录并查看Dashboard
   - 验证登录流程
   - 验证Dashboard数据显示（余额、盈亏）
   - 截图：`full-workflow-01-login.png`, `full-workflow-02-dashboard.png`

2. **test_navigate_to_accounts** - 导航到账号管理
   - 验证菜单导航
   - 验证账号页面显示
   - 截图：`full-workflow-03-accounts.png`

3. **test_navigate_to_strategies** - 导航到策略管理
   - 验证菜单导航
   - 验证策略页面显示
   - 截图：`full-workflow-04-strategies.png`

4. **test_dashboard_auto_refresh** - Dashboard自动刷新
   - 验证30秒轮询机制
   - 监听API调用
   - 截图：`full-workflow-05-dashboard-after-refresh.png`

5. **test_view_bet_orders** - 查看投注订单
   - 验证投注记录页面
   - 验证订单表格显示
   - 截图：`full-workflow-06-bet-orders.png`

6. **test_logout** - 登出功能
   - 验证登出流程
   - 验证返回登录页面
   - 截图：`full-workflow-07-logout.png`

### test_login_workflow.py

1. **test_successful_login** - 成功登录
2. **test_failed_login** - 登录失败
3. **test_navigation_after_login** - 登录后导航

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

### 修改视口大小

```python
context = await browser.new_context(
    viewport={"width": 1920, "height": 1080},  # 修改分辨率
    locale="zh-CN",
)
```

## 查看测试结果

### 截图

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
浏览器E2E测试 - 登录并查看Dashboard
============================================================

[步骤1] 打开登录页面...
[步骤2] 登录系统...
[步骤3] 验证Dashboard显示...
  ✓ 总余额: 20000.00
  ✓ 当日盈亏: +100.50
  ✓ 总盈亏: +500.00

✓ Dashboard显示验证通过
```

## 调试技巧

### 1. 暂停执行

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

## 快速开始脚本

创建 `run_browser_e2e.ps1`:

```powershell
# 检查Playwright是否安装
python -c "import playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装Playwright..."
    pip install playwright pytest-playwright
    playwright install chromium
}

# 创建截图目录
if (-not (Test-Path "screenshots")) {
    mkdir screenshots
}

# 检查服务是否运行
$backend = Test-NetConnection -ComputerName localhost -Port 8888 -InformationLevel Quiet
$frontend = Test-NetConnection -ComputerName localhost -Port 5173 -InformationLevel Quiet

if (-not $backend) {
    Write-Host "⚠️ 后端服务未运行，请先启动: uvicorn app.main:app --host 0.0.0.0 --port 8888"
    exit 1
}

if (-not $frontend) {
    Write-Host "⚠️ 前端服务未运行，请先启动: cd frontend && pnpm dev"
    exit 1
}

# 运行测试
Write-Host "运行浏览器E2E测试..."
pytest tests/e2e/browser/ -v -s
```

运行：
```powershell
cd backend
.\run_browser_e2e.ps1
```

## 下一步

1. 运行基础测试验证环境配置
2. 查看截图确认测试执行
3. 根据需要添加更多测试用例
4. 集成到CI/CD流程

## 参考资料

- [Playwright文档](https://playwright.dev/python/)
- [pytest-playwright文档](https://github.com/microsoft/playwright-pytest)
- [E2E测试最佳实践](../E2E_V2_REQUIREMENTS.md)
