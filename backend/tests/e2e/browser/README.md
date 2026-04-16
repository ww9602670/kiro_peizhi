# 浏览器E2E测试

真正的端到端测试，通过Playwright自动化Chrome浏览器来模拟用户操作。

## 安装依赖

```bash
# 安装Playwright
pip install playwright pytest-playwright

# 安装浏览器驱动
playwright install chromium
```

## 运行前准备

浏览器E2E测试需要同时运行前端和后端服务：

### 1. 启动后端服务
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

### 2. 启动前端服务
```bash
cd frontend
pnpm dev
# 前端默认运行在 http://localhost:5173
```

### 3. 创建截图目录
```bash
mkdir -p screenshots
```

## 运行测试

```bash
# 运行所有浏览器E2E测试
pytest tests/e2e/browser/ -v -s

# 运行特定测试
pytest tests/e2e/browser/test_login_workflow.py -v -s

# 无头模式运行（不显示浏览器窗口）
pytest tests/e2e/browser/ -v -s --headed=false
```

## 测试结构

```
browser/
├── conftest.py              # Playwright fixtures
├── pages/                   # Page Object Model
│   ├── login_page.py        # 登录页面对象
│   ├── dashboard_page.py    # Dashboard页面对象
│   ├── accounts_page.py     # 账号管理页面对象
│   └── strategies_page.py   # 策略管理页面对象
└── test_*.py                # 测试用例
```

## Page Object Model (POM)

使用POM模式来组织测试代码：

```python
# 页面对象封装页面元素和操作
class LoginPage:
    def __init__(self, page, base_url):
        self.page = page
        self.username_input = 'input[type="text"]'
        self.password_input = 'input[type="password"]'
    
    async def login(self, username, password):
        await self.page.fill(self.username_input, username)
        await self.page.fill(self.password_input, password)
        await self.page.click('button[type="submit"]')

# 测试用例使用页面对象
async def test_login(page, frontend_url):
    login_page = LoginPage(page, frontend_url)
    await login_page.goto()
    await login_page.login("admin", "admin123")
```

## 调试技巧

### 1. 慢速模式
在 `conftest.py` 中设置 `slow_mo=500` 可以让每个操作延迟500ms，便于观察。

### 2. 截图
```python
await page.screenshot(path="screenshots/debug.png")
```

### 3. 暂停执行
```python
await page.pause()  # 打开Playwright Inspector
```

### 4. 查看浏览器控制台
```python
page.on("console", lambda msg: print(f"Console: {msg.text}"))
```

## 与API测试的对比

| 特性 | API测试 | 浏览器E2E测试 |
|------|---------|---------------|
| 测试范围 | 后端API | 前端UI + 后端API |
| 执行速度 | 快 (秒级) | 慢 (分钟级) |
| 稳定性 | 高 | 中等 (UI变化影响) |
| 调试难度 | 低 | 中等 |
| 用户体验验证 | 否 | 是 |
| CI/CD适用性 | 高 | 中等 |

## 最佳实践

1. **使用Page Object Model**: 将页面元素和操作封装到页面对象中
2. **等待策略**: 使用 `wait_for_selector` 而不是 `wait_for_timeout`
3. **截图记录**: 在关键步骤截图，便于调试
4. **独立测试**: 每个测试应该独立，不依赖其他测试的状态
5. **清理数据**: 测试后清理创建的数据
6. **选择器策略**: 优先使用 `data-testid` 属性，其次是文本内容

## 常见问题

### Q: 测试运行时浏览器闪退
A: 检查前端服务是否正常运行在 http://localhost:5173

### Q: 找不到元素
A: 使用Playwright Inspector查看页面结构：`await page.pause()`

### Q: 测试很慢
A: 考虑使用无头模式，或减少 `slow_mo` 延迟

### Q: 如何在CI中运行
A: 使用无头模式，并确保CI环境安装了浏览器驱动
