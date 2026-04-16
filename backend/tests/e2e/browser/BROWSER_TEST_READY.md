# 浏览器E2E测试准备完成

## 已完成的工作

### 1. 创建环境准备脚本

**文件**: `backend/prepare_browser_tests.py`

功能：
- ✅ 停止所有运行中的策略
- ✅ 清除所有worker锁
- ✅ 创建/清理测试运营商账号 `e2e_browser_test`
- ✅ 删除旧的测试数据（投注记录、对账记录、告警、策略、账号）
- ✅ 显示测试账号信息和下一步操作

### 2. 更新测试用例

**文件**: `backend/tests/e2e/browser/test_full_workflow.py`

更新：
- ✅ 所有测试用例使用新账号 `e2e_browser_test`
- ✅ 密码统一为 `test123456`
- ✅ 6个完整的测试用例：
  - test_login_and_view_dashboard
  - test_navigate_to_accounts
  - test_navigate_to_strategies
  - test_dashboard_auto_refresh
  - test_view_bet_orders
  - test_logout

### 3. 创建自动化运行脚本

**文件**: `backend/run_browser_e2e_complete.ps1`

功能：
- ✅ 自动准备测试环境
- ✅ 检查后端服务状态
- ✅ 检查前端服务状态
- ✅ 自动安装Playwright（如果需要）
- ✅ 创建截图目录
- ✅ 运行浏览器E2E测试
- ✅ 显示测试结果和截图位置

### 4. 创建完整设置指南

**文件**: `backend/tests/e2e/browser/COMPLETE_SETUP_GUIDE.md`

内容：
- ✅ 快速开始指南
- ✅ 详细步骤说明
- ✅ 测试用例说明
- ✅ 测试账号信息
- ✅ 测试流程说明
- ✅ 配置选项
- ✅ 调试技巧
- ✅ 常见问题解答

---

## 测试账号信息

### 运营商账号（用于登录前端）
```
用户名: e2e_browser_test
密码: test123456
角色: operator
```

### 平台账号（用于绑定）

#### Mock平台
```
平台类型: MOCK
账号名: 任意
密码: 任意
```

#### 真实test166平台
```
平台类型: JND28WEB
账号名: testuser01
密码: test166
```

---

## 快速开始

### 方式1：使用自动化脚本（推荐）

```powershell
cd backend
.\run_browser_e2e_complete.ps1
```

### 方式2：手动执行

```powershell
# 1. 准备环境
cd backend
python prepare_browser_tests.py

# 2. 启动后端（终端1）
uvicorn app.main:app --host 0.0.0.0 --port 8888

# 3. 启动前端（终端2）
cd frontend
pnpm dev

# 4. 安装Playwright（如果需要）
cd backend
pip install playwright pytest-playwright
playwright install chromium

# 5. 创建截图目录
mkdir screenshots

# 6. 运行测试（终端3）
pytest tests/e2e/browser/test_full_workflow.py -v -s
```

---

## 测试流程

### 基础测试（无需绑定账号）

1. ✅ 登录系统
2. ✅ 查看Dashboard
3. ✅ 导航到账号管理
4. ✅ 导航到策略管理
5. ✅ 验证Dashboard自动刷新
6. ✅ 查看投注记录
7. ✅ 登出

### 完整测试（绑定账号后）

1. ✅ 登录系统
2. ✅ 绑定平台账号（mock或真实test166）
3. ✅ 创建策略
4. ✅ 启动策略
5. ✅ 观察自动投注
6. ✅ 验证余额变化
7. ✅ 查看投注记录
8. ✅ 验证结算
9. ✅ 停止策略
10. ✅ 登出

---

## 测试用例

### 1. test_login_and_view_dashboard
- 测试登录功能
- 验证Dashboard数据显示
- 截图：`full-workflow-01-login.png`, `full-workflow-02-dashboard.png`

### 2. test_navigate_to_accounts
- 测试导航到账号管理
- 验证页面元素
- 截图：`full-workflow-03-accounts.png`

### 3. test_navigate_to_strategies
- 测试导航到策略管理
- 验证页面元素
- 截图：`full-workflow-04-strategies.png`

### 4. test_dashboard_auto_refresh
- 测试Dashboard 30秒自动刷新
- 监听API调用
- 截图：`full-workflow-05-dashboard-after-refresh.png`

### 5. test_view_bet_orders
- 测试投注记录页面
- 验证订单表格
- 截图：`full-workflow-06-bet-orders.png`

### 6. test_logout
- 测试登出功能
- 验证返回登录页面
- 截图：`full-workflow-07-logout.png`

---

## 文件结构

```
backend/
├── prepare_browser_tests.py              # 环境准备脚本
├── run_browser_e2e_complete.ps1          # 自动化运行脚本
├── screenshots/                          # 测试截图目录
└── tests/e2e/browser/
    ├── COMPLETE_SETUP_GUIDE.md           # 完整设置指南
    ├── BROWSER_TEST_READY.md             # 本文件
    ├── RUN_BROWSER_TESTS.md              # 运行指南
    ├── conftest.py                       # Playwright配置
    ├── test_full_workflow.py             # 完整工作流测试
    └── pages/
        ├── login_page.py                 # 登录页面对象
        └── dashboard_page.py             # Dashboard页面对象
```

---

## 下一步操作

### 1. 准备环境

```powershell
cd backend
python prepare_browser_tests.py
```

### 2. 启动服务

**终端1 - 后端**:
```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

**终端2 - 前端**:
```powershell
cd frontend
pnpm dev
```

### 3. 运行测试

**终端3 - 测试**:
```powershell
cd backend
.\run_browser_e2e_complete.ps1
```

或手动运行：
```powershell
pytest tests/e2e/browser/test_full_workflow.py -v -s
```

---

## 验证清单

在运行测试前，请确认：

- [ ] 后端服务运行在 http://localhost:8888
- [ ] 前端服务运行在 http://localhost:5173
- [ ] 已运行 `prepare_browser_tests.py` 准备环境
- [ ] 已安装 Playwright: `pip install playwright pytest-playwright`
- [ ] 已安装 Chromium: `playwright install chromium`
- [ ] 已创建截图目录: `mkdir screenshots`
- [ ] 测试账号已创建: `e2e_browser_test` / `test123456`

---

## 支持的测试场景

### Mock平台测试
- ✅ 快速测试
- ✅ 无需真实平台账号
- ✅ 模拟投注和结算
- ✅ 适合开发和调试

### 真实test166平台测试
- ✅ 真实API调用
- ✅ 真实余额变化
- ✅ 真实投注和结算
- ✅ 完整的端到端验证

---

## 常见问题

### Q: 如何切换测试账号？

A: 编辑 `prepare_browser_tests.py` 和 `test_full_workflow.py` 中的账号信息。

### Q: 如何查看浏览器操作？

A: 在 `conftest.py` 中设置 `headless=False` 和 `slow_mo=500`。

### Q: 如何调试测试失败？

A: 
1. 查看测试输出
2. 查看截图 `screenshots/`
3. 使用 `await page.pause()` 暂停执行
4. 查看浏览器控制台

### Q: 如何添加新的测试用例？

A: 参考现有测试用例，在 `test_full_workflow.py` 中添加新的测试方法。

---

## 参考文档

- [完整设置指南](./COMPLETE_SETUP_GUIDE.md)
- [运行指南](./RUN_BROWSER_TESTS.md)
- [Playwright文档](https://playwright.dev/python/)
- [E2E测试最佳实践](../E2E_V2_REQUIREMENTS.md)

---

## 总结

✅ 环境准备脚本已创建
✅ 测试用例已更新
✅ 自动化运行脚本已创建
✅ 完整文档已准备

现在可以开始运行浏览器E2E测试了！

**推荐命令**:
```powershell
cd backend
.\run_browser_e2e_complete.ps1
```

祝测试顺利！🎉
