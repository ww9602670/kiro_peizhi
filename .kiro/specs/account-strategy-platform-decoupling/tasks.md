# 账号与策略平台类型彻底解耦任务

## 全局硬约束

1. 不允许只修前端报错而保留旧运行模型。
2. 不允许账号继续作为 `JND28WEB/JND282` 的永久绑定主体。
3. 不允许同一账号 worker 继续拿单一 `_platform_type` 跑两类 JND 策略。
4. 不允许网盘与 2.0 继续共用同一份账号赔率缓存。
5. 不允许订单继续缺少实际执行平台快照。
6. 旧兼容代码必须有明确删除 Gate，不能无限期共存。

## Phase 0. 审计与冻结

- [ ] 列出所有依赖账号层 `platform_type` 的后端路径
- [ ] 列出所有依赖账号层 `platform_type` 的前端路径
- [ ] 列出所有依赖账号层 `platform_type` 的测试路径
- [ ] 统计库内 JND 重复账号样本
- [ ] 输出 canonical account 选择规则

验证阀门：

- [ ] 能输出一份“重复 JND 账号清单”
- [ ] 能输出一份“旧链路依赖清单”

## Phase 1. Schema 扩展

涉及文件：

- [backend/app/database.py](H:/d/bocai_web/backend/app/database.py)
- [backend/app/models/db_ops.py](H:/d/bocai_web/backend/app/models/db_ops.py)

任务：

- [ ] 给 `gambling_accounts` 增加 `game_type`
- [ ] 新建 `account_platform_sessions`
- [ ] 给 `account_odds` 增加 `platform_type`
- [ ] 给 `bet_orders` 增加 `actual_platform_type`
- [ ] 为新字段补索引与唯一键
- [ ] 提供幂等迁移脚本
- [ ] 提供 dry-run 迁移检查脚本

验证阀门：

- [ ] 空库初始化通过
- [ ] 老库增量迁移通过
- [ ] 不破坏现有数据读取

失败即停：

- [ ] 任何迁移脚本不能安全重跑
- [ ] 新唯一键会直接导致老数据迁移失败且无报告

## Phase 2. 账号层改为 game_type

涉及文件：

- [backend/app/schemas/account.py](H:/d/bocai_web/backend/app/schemas/account.py)
- [backend/app/api/accounts.py](H:/d/bocai_web/backend/app/api/accounts.py)
- [frontend/src/pages/operator/Accounts.tsx](H:/d/bocai_web/frontend/src/pages/operator/Accounts.tsx)
- [frontend/src/types/api/account.ts](H:/d/bocai_web/frontend/src/types/api/account.ts)

任务：

- [ ] 账号 schema 从 `platform_type` 切到 `game_type`
- [ ] 绑定账号接口改为只校验账号与地址，不再锁死 JND 平台类型
- [ ] 前端账号页只显示“游戏类型”
- [ ] JND 账号允许的策略平台列表通过接口或前端映射得出
- [ ] 手动登录入口改名为“验证账号”或按新语义调整

验证阀门：

- [ ] 新绑定 JND 账号时，不再要求选择 `JND28WEB/JND282`
- [ ] 新绑定 LUCKYSB 账号时，行为不回退
- [ ] 老账号读取仍兼容

## Phase 3. 策略层改为按 game_type 兼容校验

涉及文件：

- [backend/app/schemas/strategy.py](H:/d/bocai_web/backend/app/schemas/strategy.py)
- [backend/app/api/strategies.py](H:/d/bocai_web/backend/app/api/strategies.py)
- [frontend/src/pages/operator/StrategyForm.tsx](H:/d/bocai_web/frontend/src/pages/operator/StrategyForm.tsx)
- [frontend/src/types/api/strategy.ts](H:/d/bocai_web/frontend/src/types/api/strategy.ts)

任务：

- [ ] 删除 `strategy.platform_type == account.platform_type` 强绑定校验
- [ ] 新增 `game_type -> allowed platform_type` 兼容校验
- [ ] 创建策略时保持策略自身 `platform_type`
- [ ] 更新策略时允许在停用状态切换 JND 平台
- [ ] 前端不再提交 `WEB/2.0`
- [ ] 前端显示仍用“网盘 / 2.0”中文，但提交值必须是接口值

验证阀门：

- [ ] 同一 JND28 账号可成功创建网盘策略
- [ ] 同一 JND28 账号可成功创建 2.0 策略
- [ ] 同一 JND28 账号可同时存在两类策略
- [ ] 不再出现 `platform_type must match account platform_type`

失败即停：

- [ ] 如果只是前端映射修正，但后端仍会覆盖策略 `platform_type`

## Phase 4. 运行时拆分为 `(account_id, platform_type)` worker

涉及文件：

- [backend/app/engine/manager.py](H:/d/bocai_web/backend/app/engine/manager.py)
- [backend/app/engine/worker.py](H:/d/bocai_web/backend/app/engine/session.py)
- [backend/app/engine/poller.py](H:/d/bocai_web/backend/app/engine/poller.py)
- [backend/app/engine/executor.py](H:/d/bocai_web/backend/app/engine/executor.py)
- [backend/app/engine/settlement.py](H:/d/bocai_web/backend/app/engine/settlement.py)
- [backend/app/engine/reconciler.py](H:/d/bocai_web/backend/app/engine/reconciler.py)
- [backend/app/engine/risk.py](H:/d/bocai_web/backend/app/engine/risk.py)

任务：

- [ ] manager registry 改为复合键
- [ ] restore/start/stop 按 `(account_id, platform_type)` 分组
- [ ] 每个 worker 固定自己的 `platform_type`
- [ ] risk 按平台会话检查
- [ ] settlement / reconciler 优先使用订单 `actual_platform_type`
- [ ] kill switch 能停掉同账号全部平台 worker

验证阀门：

- [ ] 同账号下网盘和 2.0 同时运行时，manager 能拉起两个 worker
- [ ] 两个 worker 互不覆盖对方的运行时状态
- [ ] 一个 worker 掉线不应把另一 worker 一并判死

失败即停：

- [ ] 仍然存在“取第一条策略平台启动整账号 worker”的代码路径

## Phase 5. 会话与赔率隔离

涉及文件：

- [backend/app/engine/session.py](H:/d/bocai_web/backend/app/engine/session.py)
- [backend/app/api/odds.py](H:/d/bocai_web/backend/app/api/odds.py)
- [backend/app/models/db_ops.py](H:/d/bocai_web/backend/app/models/db_ops.py)

任务：

- [ ] 会话状态写入 `account_platform_sessions`
- [ ] 账号表单 `session_token` 读取逻辑迁移到平台会话表
- [ ] 赔率按 `(account_id, platform_type, key_code)` 存储
- [ ] 刷新赔率接口要求显式 `platform_type`
- [ ] 确认赔率动作要求显式 `platform_type`

验证阀门：

- [ ] 刷新网盘赔率不会污染 2.0
- [ ] 刷新 2.0 赔率不会污染网盘
- [ ] 风控检查当前平台 token 失效时只阻断当前平台

## Phase 6. JND 重复账号迁移与合并

涉及文件：

- 迁移脚本新增目录
- [backend/app/models/db_ops.py](H:/d/bocai_web/backend/app/models/db_ops.py)

任务：

- [ ] 实现重复账号检测脚本
- [ ] 实现 dry-run 合并脚本
- [ ] 实现正式合并脚本
- [ ] 迁移策略、订单、对账、赔率到 canonical account
- [ ] 输出每批次迁移报告

验证阀门：

- [ ] dry-run 报告包含 canonical account、迁移对象数量、冲突说明
- [ ] 正式迁移后策略数与订单数不减少
- [ ] JND 重复账号被收敛为单账号

失败即停：

- [ ] 任何脚本会把余额做求和
- [ ] 任何脚本会改写历史订单所属平台快照

## Phase 7. 前端收口

涉及文件：

- [frontend/src/pages/operator/Accounts.tsx](H:/d/bocai_web/frontend/src/pages/operator/Accounts.tsx)
- [frontend/src/pages/operator/StrategyForm.tsx](H:/d/bocai_web/frontend/src/pages/operator/StrategyForm.tsx)
- [frontend/src/pages/operator/Strategies.tsx](H:/d/bocai_web/frontend/src/pages/operator/Strategies.tsx)
- [frontend/src/pages/operator/Dashboard.tsx](H:/d/bocai_web/frontend/src/pages/operator/Dashboard.tsx)
- [frontend/src/api/accounts.ts](H:/d/bocai_web/frontend/src/api/accounts.ts)
- [frontend/src/api/strategies.ts](H:/d/bocai_web/frontend/src/api/strategies.ts)

任务：

- [ ] 账号页只展示游戏类型
- [ ] 策略页只展示策略自身的平台类型
- [ ] 同账号下双平台策略展示清晰
- [ ] 赔率刷新入口显式选择平台
- [ ] 删除前端 `WEB/2.0` 伪值和相关兼容分支

验证阀门：

- [ ] 界面上不再出现内部伪值提交
- [ ] 同账号双平台策略对操作者可读

## Phase 8. 删旧链路

涉及文件：

- 所有仍保留 legacy fallback 的位置

必须删除：

- [ ] 账号 schema/接口里的旧 `platform_type` 绑定链路
- [ ] `_validate_platform_type_matches_account_or_raise`
- [ ] 账号表 `session_token` 单一会话风控链路
- [ ] `account_odds` 单账号缓存链路
- [ ] manager 第一条策略平台恢复逻辑
- [ ] 前端 `WEB/2.0` 伪平台值

删除 Gate：

- [ ] 新迁移脚本跑完
- [ ] fallback 命中统计为 0
- [ ] 后端目标测试全绿
- [ ] 前端目标测试全绿
- [ ] 人工回归通过

## 必跑验证

### 后端

- [ ] `tests/test_accounts.py`
- [ ] `tests/test_strategies.py`
- [ ] `tests/test_engine_manager.py`
- [ ] `tests/test_worker.py`
- [ ] `tests/test_session.py`
- [ ] `tests/test_risk.py`
- [ ] `tests/test_reconciler.py`
- [ ] `tests/test_settlement.py`
- [ ] 新增迁移脚本测试
- [ ] 新增 odds 平台隔离测试

### 前端

- [ ] `src/pages/operator/Accounts.test.tsx`
- [ ] `src/pages/operator/StrategyForm.test.tsx`
- [ ] `src/pages/operator/Strategies.test.tsx`
- [ ] 新增赔率平台选择测试

## 最终收口条件

- [ ] 新账号模型已切到 `game_type`
- [ ] 新策略模型允许同账号双平台
- [ ] 运行时已拆为复合键 worker
- [ ] 会话与赔率已平台隔离
- [ ] 历史重复 JND 账号已合并
- [ ] 旧链路已删除，不再保留长期兼容分支
