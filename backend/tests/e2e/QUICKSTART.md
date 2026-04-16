# E2E测试快速开始指南

## 快速运行

### 1. 启动后端服务

在一个终端窗口中：

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

### 2. 运行E2E测试

在另一个终端窗口中：

```bash
cd backend
pytest tests/e2e/test_e2e_full_workflow.py::TestE2EFullWorkflow::test_e2e_full_workflow_happy_path -v -s
```

## 测试说明

这个测试将执行以下步骤：

1. ✓ 以`e2e_final_test`身份登录
2. ✓ 绑定`test166`测试账号
3. ✓ 账号登录（获取余额和赔率）
4. ✓ 创建投注策略
5. ✓ 启动策略（触发Worker）
6. ✓ 等待自动投注（最多60秒）
7. ✓ 等待结算（最多120秒）
8. ✓ 验证结算数据
9. ✓ 检查Dashboard数据
10. ✓ 停止策略并清理

## 预期输出

```
============================================================
E2E完整工作流测试 - 成功路径
============================================================

[登录] 开始执行...
✓ 登录成功: operator_id=X

[绑定账号] 开始执行...
✓ 绑定账号成功: account_id=X

[账号登录] 开始执行...
✓ 账号登录成功: status=online, balance=XXXX

[创建策略] 开始执行...
✓ 创建策略成功: strategy_id=X, name=E2E测试策略

[启动策略] 开始执行...
✓ 启动策略成功: strategy_id=X, status=running

等待自动投注...
等待投注... (超时: 60秒)
✓ 检测到投注: order_id=X, status=bet_success, amount=1000

等待结算...
等待结算... (超时: 120秒)
✓ 订单已结算: order_id=X, is_win=X, pnl=X

检查Dashboard...
✓ Dashboard数据: balance=X, daily_pnl=X, total_pnl=X

停止策略...
✓ 停止策略成功: strategy_id=X, status=stopped

============================================================
✓ E2E完整工作流测试通过
============================================================
```

## 故障排除

### 测试超时

如果测试在"等待投注"阶段超时：
- 检查平台是否处于开盘状态
- 检查Worker是否正常启动
- 查看后端日志

### 账号登录失败

如果账号登录失败：
- 确认test166账号可用
- 检查账号密码是否正确
- 验证平台API连接

### 其他问题

查看完整文档：`backend/tests/e2e/README.md`
