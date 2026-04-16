# E2E测试框架 v2.0 - 改进需求

## 核心改进目标

### 1. 开奖驱动的结算测试 ⚠️ 高优先级

**当前问题**: 
- 结算测试使用固定120秒超时
- 无法准确等待开奖
- 测试经常超时失败

**改进方案**:
- 投注后查询当期期号
- 轮询开奖API等待该期开奖结果
- 开奖后立即触发结算验证
- 动态超时（基于开奖倒计时）

**实现要点**:
```python
async def wait_for_lottery_result(issue: str, timeout: int = 300):
    """等待指定期号的开奖结果"""
    # 轮询 /api/v1/lottery/results/{issue}
    # 或查询数据库 lottery_results 表
    pass
```

### 2. 真实平台API测试 ⚠️ 高优先级

**当前问题**:
- 大部分测试使用模拟模式（simulation=1）
- 无法验证真实余额扣减和结算
- 无法验证平台API集成

**改进方案**:
- 使用 simulation=0 进行真实投注
- 验证真实余额扣减（投注时）
- 验证真实余额返还（结算时）
- 验证平台赔率数据
- 验证平台开奖数据

**测试用例**:
- test_real_bet_balance_deduction
- test_real_settlement_balance_update
- test_real_odds_consistency
- test_real_lottery_result_accuracy

### 3. 余额一致性验证 ⚠️ 高优先级

**验证点**:
- 投注前余额
- 投注后余额 = 投注前 - 投注金额
- 结算后余额 = 投注后 + 返还金额
- 返还金额 = 本金 + 盈亏

**实现**:
```python
class BalanceTracker:
    def __init__(self, account_id):
        self.account_id = account_id
        self.snapshots = []
    
    async def snapshot(self, label: str):
        balance = await get_account_balance(self.account_id)
        self.snapshots.append((label, balance))
    
    def verify_deduction(self, bet_amount):
        before = self.snapshots[-2][1]
        after = self.snapshots[-1][1]
        assert after == before - bet_amount
```

### 4. 浏览器E2E测试 📋 中优先级

**目标**: 使用Playwright模拟真实用户操作

**测试场景**:
- 登录 → 绑定账号 → 创建策略 → 启动策略
- 观察Dashboard实时更新
- 验证余额显示
- 验证投注记录显示
- 验证结算结果显示

**实现框架**: 已创建 `backend/tests/e2e/browser/`

## 实施计划

### Phase 1: 开奖驱动结算 (1-2天)

- [ ] 实现 `wait_for_lottery_result()` 方法
- [ ] 更新 `wait_for_settlement()` 使用开奖驱动
- [ ] 更新所有结算测试用例
- [ ] 测试验证

### Phase 2: 真实平台测试 (2-3天)

- [ ] 创建真实投注测试用例
- [ ] 实现余额追踪器
- [ ] 验证余额一致性
- [ ] 验证赔率和开奖数据
- [ ] 测试验证

### Phase 3: 浏览器测试 (3-5天)

- [ ] 完善Playwright测试框架
- [ ] 实现登录流程测试
- [ ] 实现账号管理测试
- [ ] 实现策略管理测试
- [ ] 实现Dashboard轮询测试
- [ ] 测试验证

## 成功标准

1. 结算测试不再超时
2. 真实投注余额验证通过
3. 浏览器测试覆盖核心流程
4. 所有测试可在CI/CD中运行
