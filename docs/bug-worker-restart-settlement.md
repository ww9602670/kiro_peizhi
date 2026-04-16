# Bug 记录：Worker 重启导致结算混乱

## 发现时间
2026-03-16

## 修复时间
2026-03-16

## 状态
已修复 ✅

## 现象
新增策略或新增账号+策略后，下注和结算出现混乱：
- 部分期号订单被标记为 settle_failed（"补结算数据过期"）
- 实际上这些订单只是刚下注几秒钟，还没到开奖时间
- 平台投注记录（bet_order_platform_records）缺失，因为结算流程被中断后根本没有去拉取

## 影响范围
- 同一平台账号下新增策略时触发
- 停止策略时如果同账号还有其他策略也可能触发

## 根因分析

### Bug 1：start_worker() 暴力重启 Worker
- 文件：`backend/app/engine/manager.py` → `start_worker()`
- 文件：`backend/app/api/strategies.py` → `_transition_strategy()`
- 当启动新策略时，`_transition_strategy()` 调用 `engine.start_worker()`
- `start_worker()` 发现已有 Worker 在运行，先 `existing.stop()`（cancel task 强制中断），再创建新 Worker
- 这会中断正在进行的结算周期（投注→等待开奖→结算 的完整流程被打断）

### Bug 2：_recover_unsettled_orders() 对未开奖期号直接标记 settle_failed
- 文件：`backend/app/engine/worker.py` → `_recover_unsettled_orders()`
- 新 Worker 启动后执行补结算，调用 `adapter.get_lottery_results(count=50)` 获取历史开奖结果
- 对于刚下注几秒钟的期号，历史开奖结果中自然找不到（还没开奖）
- 代码直接标记为 settle_failed 并发告警"补结算数据过期"
- 实际上应该留给正常结算周期处理
- **连锁影响**：因为直接走了 settle_failed 分支，根本没有调用 `_settle_real()` → `get_bet_history()`，所以 `bet_order_platform_records` 表中也没有该期号的平台记录

### Bug 3：停止策略时缺少 remove_strategy() 调用
- 文件：`backend/app/api/strategies.py` → `_transition_strategy()` 停止分支
- 当停止一个策略但同账号还有其他 running 策略时，不停止 Worker（逻辑正确）
- 但没有调用 `worker.remove_strategy()` 从 Worker 中移除该策略
- 导致已停止的策略仍然在 Worker 中产生投注信号

## 修复方案

### 修复 1：manager.py start_worker() 改为热更新模式 ✅
- 如果已有 Worker 在运行且状态正常，不再 stop + 重建
- 新增 `_hot_update_worker()` 方法：对比现有策略和目标策略，增删差异部分
- 使用 `worker.add_strategy()` 热插入新策略，`worker.remove_strategy()` 移除旧策略
- Worker 主循环不中断，正在进行的结算周期不受影响

### 修复 2：worker.py _recover_unsettled_orders() 增加时间保护 ✅
- 新增 `_has_recent_orders()` 方法：检查指定期号最近一笔订单的 bet_at 时间
- 距今不超过 3 分钟的新订单跳过，留给正常结算周期处理
- 只对超过 3 分钟的老订单执行补结算/标记 settle_failed
- 兼容 bet_at 和 created_at 字段，处理字段缺失的情况

### 修复 3：strategies.py _transition_strategy() 添加 remove_strategy() ✅
- 停止场景：如果同账号还有其他 running 策略，调用 `worker.remove_strategy()` 移除该策略
- 暂停场景：同样调用 `worker.remove_strategy()` 从 Worker 中移除该策略
- Worker 继续运行，只是不再为已停止/暂停的策略产生投注信号

## 数据证据
- 期号 3408759：strategy 498 订单 settle_failed（Worker 重启导致）
- 期号 3408760：全部 4 笔订单 settle_failed（告警"补结算数据过期"）
- 期号 3408761：全部 4 笔订单正常结算（Worker 稳定后恢复正常）
- 期号 3408763：4 笔订单 settle_failed，且 bet_order_platform_records 无记录（Bug 1+2 连锁效应）

## 测试验证
- 157 个现有测试全部通过（test_engine_manager + test_worker + test_strategies）
