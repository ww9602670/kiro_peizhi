# E2E完整工作流测试

## 概述

本目录包含自动投注平台的端到端（E2E）集成测试，验证从用户登录、账号绑定、策略创建、自动投注到结算的完整工作流程。

## 测试目的

- 验证完整业务流程的正确性
- 确保前后端API集成无误
- 验证数据库状态与API响应的一致性
- 测试Worker自动投注和结算的端到端流程
- 验证前端轮询机制能正确反映后端状态变化

## 测试环境要求

### 1. 后端服务

E2E测试需要后端服务运行：

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

### 2. 测试账号

测试使用以下账号：

- **用户账号**: `e2e_final_test` / `test123456`
- **博彩账号**: `test166` / `test166`
- **平台类型**: `JND28WEB`

确保这些账号在数据库中存在且可用。

### 3. 数据库

测试使用真实的SQLite数据库（`data/bocai.db`），而不是内存数据库。这样可以：
- 在测试过程中查看数据库状态
- 便于调试和问题排查
- 测试完成后会自动清理测试数据

## 运行测试

### 运行所有E2E测试

```bash
cd backend
pytest tests/e2e/ -v -s
```

### 运行单个测试用例

```bash
# 完整工作流测试
pytest tests/e2e/test_e2e_full_workflow.py::TestE2EFullWorkflow::test_e2e_full_workflow_happy_path -v -s

# 多期投注测试
pytest tests/e2e/test_e2e_full_workflow.py::TestE2EFullWorkflow::test_e2e_multiple_periods -v -s

# Dashboard轮询测试
pytest tests/e2e/test_e2e_full_workflow.py::TestE2EFullWorkflow::test_e2e_dashboard_polling -v -s

# 登录失败测试
pytest tests/e2e/test_e2e_full_workflow.py::TestE2EFullWorkflow::test_e2e_login_failure -v -s
```

### 参数说明

- `-v`: 显示详细输出
- `-s`: 显示print输出（用于查看测试进度）
- `--tb=short`: 简化错误回溯信息

## 测试用例

### 核心流程测试

1. **test_e2e_full_workflow_happy_path**: 完整的成功路径测试
   - 登录 → 绑定账号 → 创建策略 → 启动策略 → 观察投注 → 验证结算 → 检查Dashboard

2. **test_e2e_multiple_periods**: 多期投注和结算测试
   - 验证跨多个期号的投注和结算
   - 验证累计盈亏计算正确

3. **test_e2e_dashboard_polling**: Dashboard轮询测试
   - 模拟前端轮询行为
   - 验证数据实时更新

### 错误场景测试

1. **test_e2e_login_failure**: 登录失败测试
   - 使用错误密码登录
   - 验证返回401错误

2. **test_e2e_account_binding_duplicate**: 账号绑定重复测试
   - 尝试绑定重复的账号
   - 验证返回409错误

3. **test_e2e_start_strategy_without_online_account**: 策略启动前置条件测试
   - 在账号未登录时启动策略
   - 验证返回400错误

## 测试架构

```
backend/tests/e2e/
├── __init__.py                    # 模块初始化
├── conftest.py                    # pytest fixtures
├── config.py                      # 测试配置
├── test_e2e_full_workflow.py     # 主测试文件
├── helpers/                       # 辅助工具
│   ├── __init__.py
│   ├── context.py                 # E2ETestContext类
│   └── utils.py                   # 工具函数
└── README.md                      # 本文档
```

## E2ETestContext类

`E2ETestContext`类封装了常用的测试操作，提供便捷的API调用方法：

### 认证方法
- `login(username, password)`: 登录
- `_get_headers()`: 获取认证头

### 账号管理方法
- `bind_account(account_name, password, platform_type)`: 绑定账号
- `account_login(account_id)`: 账号登录
- `get_account(account_id)`: 查询账号详情

### 策略管理方法
- `create_strategy(config)`: 创建策略
- `start_strategy(strategy_id)`: 启动策略
- `stop_strategy(strategy_id)`: 停止策略
- `get_strategy(strategy_id)`: 查询策略详情

### 投注观察方法
- `wait_for_bet(strategy_id, timeout)`: 等待投注完成
- `get_order(order_id)`: 查询订单详情
- `get_bet_orders(filters)`: 查询投注订单列表

### 结算观察方法
- `wait_for_settlement(order_id, timeout)`: 等待订单结算
- `get_lottery_result(issue)`: 查询开奖结果
- `verify_settlement_data(order_id)`: 验证结算数据

### Dashboard方法
- `get_dashboard()`: 获取Dashboard数据
- `verify_dashboard_data(expected)`: 验证Dashboard数据

## 测试数据清理

测试使用`e2e_cleanup` fixture自动清理测试数据：

1. 在测试开始时记录创建的资源ID
2. 在测试结束后自动删除：
   - 策略
   - 投注订单
   - 账号
   - Operator（如果是测试创建的）

## 注意事项

### 1. 测试时间

E2E测试需要等待真实的投注和结算流程，因此执行时间较长：
- 单个测试用例：1-3分钟
- 完整测试套件：5-10分钟

### 2. 平台状态

测试依赖真实的平台API，因此：
- 需要平台处于开盘状态
- 需要有足够的时间窗口进行投注
- 如果平台封盘，测试可能需要等待下一期

### 3. 并发限制

不建议并行运行E2E测试，因为：
- 使用相同的测试账号
- 可能产生数据冲突
- 难以追踪测试结果

### 4. 调试技巧

如果测试失败，可以：
1. 查看测试输出的详细日志
2. 检查数据库状态（`data/bocai.db`）
3. 查看后端服务日志
4. 使用`-s`参数查看print输出

## 故障排除

### 问题：测试超时

**原因**：平台未开盘或封盘时间过长

**解决**：
- 检查平台状态
- 增加超时时间
- 等待平台开盘后再运行测试

### 问题：账号登录失败

**原因**：test166账号不可用或密码错误

**解决**：
- 检查账号配置（`config.py`）
- 验证账号在平台上可用
- 检查账号余额是否充足

### 问题：Worker未启动

**原因**：后端服务未运行或Engine未初始化

**解决**：
- 确保后端服务正在运行
- 检查后端日志
- 验证Engine Manager状态

## 相关文档

- [需求文档](../../../.kiro/specs/e2e-full-workflow-test/requirements.md)
- [设计文档](../../../.kiro/specs/e2e-full-workflow-test/design.md)
- [任务列表](../../../.kiro/specs/e2e-full-workflow-test/tasks.md)
