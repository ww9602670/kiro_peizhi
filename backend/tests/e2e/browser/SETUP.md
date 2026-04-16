# 浏览器E2E测试环境配置

## 第1步：安装Playwright

```bash
cd backend
pip install playwright pytest-playwright
```

## 第2步：安装浏览器驱动

```bash
playwright install chromium
```

这会下载Chromium浏览器（约150MB）。

## 第3步：验证安装

```bash
python -c "from playwright.sync_api import sync_playwright; print('Playwright installed successfully!')"
```

## 第4步：启动服务

### 启动后端（终端1）
```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8888
```

### 启动前端（终端2）
```bash
cd frontend
pnpm dev
```

## 第5步：运行测试

```bash
cd backend
pytest tests/e2e/browser/test_login_workflow.py -v -s
```

## 快速启动（Windows）

```powershell
cd backend
.\tests\e2e\browser\run_browser_e2e.ps1
```

## 故障排除

### 问题1: ModuleNotFoundError: No module named 'playwright'
**解决**: 运行 `pip install playwright pytest-playwright`

### 问题2: Executable doesn't exist
**解决**: 运行 `playwright install chromium`

### 问题3: 前端服务连接失败
**解决**: 确保前端服务运行在 http://localhost:5173

### 问题4: 后端服务连接失败
**解决**: 确保后端服务运行在 http://localhost:8888

## 配置选项

在 `conftest.py` 中可以配置：

```python
browser = await p.chromium.launch(
    headless=False,  # True=无头模式，False=显示浏览器
    slow_mo=500,     # 每个操作延迟ms
)
```

## 下一步

1. 查看 `README.md` 了解测试结构
2. 查看 `test_login_workflow.py` 了解示例测试
3. 根据实际前端UI调整选择器
4. 添加更多测试用例
