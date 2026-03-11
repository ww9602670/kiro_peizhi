# 彩票倒计时显示与下注时机优化 - 任务列表

## 1. 后端实现

### 1.1 Schema 定义
- [ ] 1.1.1 创建 `backend/app/schemas/lottery.py`
- [ ] 1.1.2 定义 `LotteryState` 枚举（1=开盘，2=封盘，3=开奖，0=未知）
- [ ] 1.1.3 定义 `CurrentInstallResponse` schema（使用 close_countdown_sec/open_countdown_sec）
- [ ] 1.1.4 添加示例数据和字段验证（ge=0）

### 1.2 Adapter 改进
- [ ] 1.2.1 更新 `PlatformAdapter` 抽象契约（添加 `get_current_install_detail()` 抽象方法）
- [ ] 1.2.2 在 `JNDAdapter` 实现 `get_current_install_detail()` 方法（复用现有 authenticated session）
- [ ] 1.2.3 解析平台 API 返回的 State、CloseTimeStamp、OpenTimeStamp
- [ ] 1.2.4 实现未知状态归一化（非 1/2/3 的值映射为 0）
- [ ] 1.2.5 更新 `InstallInfo` 数据类（添加 close_countdown_sec/open_countdown_sec 字段，保留兼容属性）
- [ ] 1.2.6 更新 `BetResult` 数据类（添加 error_code 和 is_retryable 属性）
- [ ] 1.2.7 迁移所有 `InstallInfo` 构造调用点（从 close_timestamp 改为 close_countdown_sec）
- [ ] 1.2.8 编写单元测试（覆盖所有状态枚举 + 未知值归一化 + 错误码映射 + 兼容属性）

### 1.3 API 端点
- [ ] 1.3.1 创建 `backend/app/api/lottery.py`
- [ ] 1.3.2 实现 `GET /api/v1/lottery/current-install` 端点（返回 Envelope 格式）
- [ ] 1.3.3 添加 JWT 认证依赖
- [ ] 1.3.4 添加限流保护
- [ ] 1.3.5 在 `main.py` 注册 router
- [ ] 1.3.6 编写 API 测试（验证 Envelope 格式）

### 1.4 Worker 改进
- [ ] 1.4.1 修改 `Worker._should_bet()` 使用 State 判断（State == 1 且 close_countdown_sec > 18）
- [ ] 1.4.2 实现 `_place_bet_with_validation()` 方法（二次校验：期号一致性 + 状态 + 时间）
- [ ] 1.4.3 实现 `_execute_bet_with_installments()` 方法（使用现有 place_bet 接口，issue 参数绑定期号）
- [ ] 1.4.4 使用 BetResult.error_code 和 is_retryable 进行错误分类
- [ ] 1.4.5 遵循"Confirmbet 零重试"原则（记录日志但不实现重试逻辑）
- [ ] 1.4.6 移除基于赔率的判断逻辑
- [ ] 1.4.7 添加详细日志（包含 state、剩余时间、期号一致性、错误码）
- [ ] 1.4.8 编写单元测试（覆盖所有状态、边界条件、期号变化场景、错误码分类）

## 2. 前端实现

### 2.1 类型定义
- [ ] 2.1.1 创建 `frontend/src/types/api/lottery.ts`
- [ ] 2.1.2 定义 `LotteryStateEnum` 枚举（1=开盘，2=封盘，3=开奖，0=未知）
- [ ] 2.1.3 定义 `CurrentInstall` 接口（使用 close_countdown_sec/open_countdown_sec）
- [ ] 2.1.4 定义 `LotteryStateDisplay` 接口
- [ ] 2.1.5 定义 `STATE_DISPLAY_MAP` 映射表

### 2.2 API 调用层
- [ ] 2.2.1 创建 `frontend/src/api/lottery.ts`
- [ ] 2.2.2 实现 `fetchCurrentInstall()` 函数（注释说明 Envelope 自动解包）
- [ ] 2.2.3 编写单元测试（验证 Envelope 解包）

### 2.3 倒计时 Hook 与组件
- [ ] 2.3.1 创建 `frontend/src/hooks/useLotteryCountdown.ts`（共享 hook，注释说明生产环境建议使用 React Query/SWR）
- [ ] 2.3.2 实现倒计时逻辑（每秒更新，钳制为非负值）
- [ ] 2.3.3 实现数据获取（每 5 秒刷新）
- [ ] 2.3.4 实现错误处理和降级提示
- [ ] 2.3.5 创建 `frontend/src/components/CountdownDisplay.tsx`
- [ ] 2.3.6 使用 `STATE_DISPLAY_MAP` 实现状态判断逻辑（含未知状态兜底）
- [ ] 2.3.7 创建 `frontend/src/components/CountdownDisplay.css`（含错误提示样式）
- [ ] 2.3.8 编写组件测试

### 2.4 页面集成
- [ ] 2.4.1 在 `Dashboard.tsx` 中集成 CountdownDisplay
- [ ] 2.4.2 在 `Strategies.tsx` 中集成 CountdownDisplay
- [ ] 2.4.3 调整页面布局

## 3. 测试验证

### 3.1 单元测试
- [ ] 3.1.1 测试 State 判断逻辑（覆盖所有枚举值 + 未知值归一化）
- [ ] 3.1.2 测试倒计时计算（含负值钳制）
- [ ] 3.1.3 测试状态转换（使用 STATE_DISPLAY_MAP + 未知状态兜底）
- [ ] 3.1.4 测试二次校验逻辑（期号一致性 + 状态 + 时间）
- [ ] 3.1.5 测试状态快速跳变场景（1→2→1）
- [ ] 3.1.6 测试跨期瞬间下注拒绝

### 3.2 集成测试
- [ ] 3.2.1 测试 API 端点返回正确数据（验证 Envelope 格式）
- [ ] 3.2.2 测试 Worker 在不同 State 下的行为（1/2/3/0）
- [ ] 3.2.3 测试二次校验在状态变化时的行为
- [ ] 3.2.4 测试二次校验在期号变化时的行为（跨期拒绝）
- [ ] 3.2.5 测试前端组件数据刷新和降级提示
- [ ] 3.2.6 测试接口短时失败恢复

### 3.3 Chrome MCP 测试
- [ ] 3.3.1 验证倒计时显示正常
- [ ] 3.3.2 验证状态切换正常
- [ ] 3.3.3 验证 API 请求正常（Probe 检测）
- [ ] 3.3.4 截图保存

### 3.4 真实下注测试（量化验收）
- [ ] 3.4.1 记录基线数据（部署前 3 个工作日，每天"赔率已改变"错误率）
- [ ] 3.4.2 定义下注机会：State=1 且 close_countdown_sec>18 的期数
- [ ] 3.4.3 部署新版本，启动策略观察下注时机
- [ ] 3.4.4 验证只在 State=1 且 close_countdown_sec>18 时下注
- [ ] 3.4.5 验证二次校验生效（日志中有期号一致性校验记录）
- [ ] 3.4.6 统计 3 个工作日数据（排除节假日、网络超时、平台维护）
- [ ] 3.4.7 验证错误率下降 >= 80%
- [ ] 3.4.8 检查告警记录和可观测性指标

## 4. 文档更新

### 4.1 API 文档
- [ ] 4.1.1 更新 `PLATFORM_API_REFERENCE.md`
- [ ] 4.1.2 添加新接口说明（含状态枚举表）
- [ ] 4.1.3 添加 Envelope 格式说明

### 4.2 用户文档
- [ ] 4.2.1 创建倒计时功能使用说明
- [ ] 4.2.2 更新截图

### 4.3 可观测性文档
- [ ] 4.3.1 记录新增监控指标
- [ ] 4.3.2 创建告警规则文档

## 5. 部署

### 5.1 代码审查
- [ ] 5.1.1 后端代码审查
- [ ] 5.1.2 前端代码审查

### 5.2 合并部署
- [ ] 5.2.1 创建 PR
- [ ] 5.2.2 通过 CI 检查
- [ ] 5.2.3 合并到 main
- [ ] 5.2.4 部署到生产环境

## 验收标准

- [ ] 仪表盘显示实时倒计时
- [ ] 策略页面显示实时倒计时
- [ ] 倒计时每秒更新
- [ ] 状态文字清晰（开盘中/封盘中/开奖中/未知）
- [ ] 状态颜色正确（开盘=绿色，封盘=红色，开奖=黄色，未知=灰色）
- [ ] API 异常时显示降级提示
- [ ] Worker 只在 State=1 且 close_countdown_sec>18 时下注
- [ ] 下注前进行二次校验（期号一致性 + 状态 + 时间）
- [ ] 下注请求使用 `place_bet(issue=expected_installments, betdata=...)` 绑定期号
- [ ] 遵循"Confirmbet 零重试"原则（记录日志但不实现重试逻辑）
- [ ] **下注成功率提升（量化）**：连续 3 个工作日，每天 >= 50 次下注机会（State=1 且 close_countdown_sec>18），"赔率已改变"错误率下降 >= 80%（排除网络超时、平台维护）
- [ ] 所有测试通过
- [ ] Chrome MCP 验证通过
- [ ] API 响应符合 Envelope 格式
- [ ] 可观测性指标正常采集

## 任务依赖与里程碑

### 里程碑 1：契约冻结（Day 1）
- 完成 1.1（Schema 定义）
- 完成 2.1（类型定义）
- 前后端契约评审通过

### 里程碑 2：后端实现（Day 2-3）
- 完成 1.2（Adapter）
- 完成 1.3（API 端点）
- 完成 1.4（Worker 改进）
- 后端单元测试通过

### 里程碑 3：前端实现（Day 4-5）
- 完成 2.2（API 调用层）
- 完成 2.3（Hook 与组件）
- 完成 2.4（页面集成）
- 前端单元测试通过

### 里程碑 4：联调与测试（Day 6-7）
- 完成 3.1-3.3（单元测试、集成测试、Chrome MCP 测试）
- 修复发现的问题

### 里程碑 5：灰度与验收（Day 8-10）
- 完成 3.4（真实下注测试）
- 完成 4（文档更新）
- 完成 5（部署）
- 达到量化验收标准
