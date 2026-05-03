# 第一阶段当前真实链路核查报告

生成时间：2026-05-03

依据文档：`docs/shared-market-worker-timing-remediation-report.md`

核查范围：只核查当前代码真实实现，不修改业务代码。

## 总体结论

当前项目已经具备共享采集器、共享快照、worker、前端倒计时、统一 session runtime 等基础链路，但与整改报告目标仍有明显差距。

最关键的不符合点：

1. 共享采集器目前只有固定 25 秒采集，没有开奖倒计时归零后的专项刷新节奏。
2. 共享快照缺失、过期或异常时，worker 会使用操作者账号本地采集。
3. worker 和前端本地采集结果都可能写回共享池。
4. worker 在下注前仍会调用第三方平台 `get_current_install()` 做倒计时重校验。
5. 结算仍在 worker 主循环内串行执行，会阻塞下一轮 worker 循环。
6. 策略创建、编辑、启动和重启恢复仍依赖 `verification_stale`。
7. 前端开奖倒计时归零后仍按 30 秒刷新，没有按整改报告的 10/10/10/5/3/1 节奏。
8. 当前公开状态只有 `shared_hit`、`local_fallback`、`processing`，还没有 `draw_pending`、`draw_wait_retry`、`market_closed`、`shared_error`、`shared_stale` 等状态契约。

## 1. 共享采集器频率

文件路径：

- `backend/app/engine/shared_market_runtime.py`

函数名：

- `SharedMarketRuntime.__init__`
- `SharedMarketRuntime._collector_loop`

当前逻辑：

共享采集器默认每 25 秒执行一次平台采集；采集异常后按 5 秒回退重试。

是否符合整改报告：

部分符合。25 秒正常采集符合；异常后 5 秒重试和整改报告希望降低请求频率、减少风控风险的目标不完全一致。

证据位置：

- `backend/app/engine/shared_market_runtime.py:25-27`
- `backend/app/engine/shared_market_runtime.py:623-630`
- `backend/app/engine/shared_market_runtime.py:1127-1160`

风险判断：

共享账号异常时，如果持续失败，5 秒一次的异常重试可能增加平台请求压力。

后续修改建议：

保留正常 25 秒采集；异常重试应接入共享状态和退避策略，避免共享账号异常时持续高频打平台。

## 2. 开奖倒计时归零后的刷新机制

文件路径：

- `backend/app/engine/shared_market_runtime.py`
- `frontend/src/hooks/useLotteryCountdown.ts`
- `backend/app/engine/worker.py`

函数名：

- `SharedMarketRuntime._collector_loop`
- `CountdownStore.start`
- `CountdownStore.fetchNow`
- `AccountWorker._main_loop`

当前逻辑：

后端共享采集器没有开奖倒计时归零后的专项刷新机制，只按固定 25 秒循环采集。前端本地倒计时归零后等待 30 秒再请求后端。worker 在结算链路中等待开奖倒计时归零，再额外等待默认 30 秒。

是否符合整改报告：

不符合。整改报告要求开奖归零后按 10 秒首刷，再按 10 秒、10 秒、5 秒、3 秒、1 秒继续专项刷新，第 6 次后每 10 秒刷新直到拿到新数据或停盘。

证据位置：

- `backend/app/engine/shared_market_runtime.py:1127-1160`
- `frontend/src/hooks/useLotteryCountdown.ts:6-8`
- `frontend/src/hooks/useLotteryCountdown.ts:123-126`
- `frontend/src/hooks/useLotteryCountdown.ts:160-168`
- `backend/app/engine/worker.py:830-841`

风险判断：

前端和 worker 都可能在开奖数据尚未更新时继续等待或误判。当前新开奖数据出现慢，主要来自固定轮询和前端 30 秒延迟，而不是专项节点刷新。

后续修改建议：

共享采集器增加开奖专项刷新调度；前端根据后端状态和 `next_draw_retry_at` 刷新；worker 只读本地共享快照，不自行高频拉平台开奖接口。

## 3. worker 获取期号、开奖、倒计时的来源

文件路径：

- `backend/app/engine/manager.py`
- `backend/app/engine/poller.py`
- `backend/app/engine/shared_market_runtime.py`
- `backend/app/engine/worker.py`

函数名：

- `EngineManager.start_worker`
- `IssuePoller._fetch_install`
- `IssuePoller.poll`
- `SharedMarketRuntime.resolve_install`
- `AccountWorker._fetch_install_with_retry`

当前逻辑：

worker 通过 `IssuePoller.poll()` 获取期号、开奖和倒计时。poller 优先调用 `shared_market_runtime.resolve_install()`。共享快照命中且新鲜时使用共享快照；共享缺失、过期或异常时会回退到操作者账号本地 `adapter.get_current_install()`。

是否符合整改报告：

部分符合。优先读共享快照符合；共享异常时直接使用操作者账号本地采集不符合第一阶段目标中的严格边界。

证据位置：

- `backend/app/engine/manager.py:300-307`
- `backend/app/engine/poller.py:127-162`
- `backend/app/engine/poller.py:181-241`
- `backend/app/engine/shared_market_runtime.py:757-869`
- `backend/app/engine/worker.py:1007-1019`

风险判断：

共享异常时，多 worker 会各自触发操作者账号采集，平台请求量会被放大。

后续修改建议：

区分 `draw_pending`、`draw_wait_retry`、`shared_error`、`shared_stale`。共享正常但开奖等待时，worker 只能读共享快照；只有共享明确异常或过期到不可用时，才允许按 worker 维度 80 秒低频回退。

## 4. worker 下注前是否调用第三方平台倒计时接口

文件路径：

- `backend/app/engine/worker.py`

函数名：

- `AccountWorker._run_due_strategy_windows`
- `AccountWorker._revalidate_group_window`

当前逻辑：

worker 在某个下注时间窗口到达后，会调用 `_revalidate_group_window()`。该函数通过 `session_runtime.run_platform_call("worker_group_revalidate_install", self.adapter.get_current_install)` 再次请求第三方平台当前期号和倒计时，然后才允许提交投注。

是否符合整改报告：

不符合。整改报告要求操作者账号的策略下注前不再额外请求平台倒计时，下注时机应基于系统已有倒计时快照判断。

证据位置：

- `backend/app/engine/worker.py:474-539`
- `backend/app/engine/worker.py:930-948`

风险判断：

同一账号多策略运行时，每个到期下注窗口都可能触发平台倒计时重校验，增加平台请求频率，也可能引发被风控或被误判封盘。

后续修改建议：

移除常规下注前的平台倒计时强校验，改为校验本地共享快照的新鲜度、期号一致性、封盘截止时间和共享状态。只有下注返回失败后，再进入账号自检、心跳、盘口状态和重登链路。

## 5. 共享快照缺失、过期、异常时是否触发操作者账号本地采集

文件路径：

- `backend/app/engine/shared_market_runtime.py`
- `backend/app/engine/poller.py`
- `backend/app/api/lottery.py`

函数名：

- `SharedMarketRuntime.resolve_install`
- `SharedMarketRuntime._fallback_local`
- `IssuePoller._fetch_install`
- `_resolve_account_current_install`
- `_fetch_local_account_install`

当前逻辑：

会触发。共享组未命中、共享快照缺失、`source_status` 非 ok、`fetched_at` 缺失、快照超过 35 秒都会进入 `_fallback_local()`，该函数调用传入的 `fetch_local_install()`。poller 捕获共享 runtime 异常后也会直接调用本地采集。前端 `/lottery/current-install` 在无可用共享快照时，也会使用操作者账号 session token 创建 adapter 并调用 `get_current_install()`。

是否符合整改报告：

不符合。整改报告要求共享正常或开奖等待状态下不能让操作者账号承担额外采集；共享异常时也只能按 80 秒低频回退，并且要明确提示。

证据位置：

- `backend/app/engine/shared_market_runtime.py:833-867`
- `backend/app/engine/shared_market_runtime.py:879-898`
- `backend/app/engine/poller.py:154-162`
- `backend/app/api/lottery.py:279-304`
- `backend/app/api/lottery.py:311-409`

风险判断：

共享池异常时，操作者前端和 worker 都可能变成额外采集源，请求量可能随着在线账号数放大。

后续修改建议：

给共享快照补齐状态字段和节流字段；前端和 worker 都要根据状态决定动作，不能一发现共享快照不新鲜就直接采集平台。

## 6. 本地采集结果是否可能写回共享池

文件路径：

- `backend/app/engine/shared_market_runtime.py`
- `backend/app/api/lottery.py`

函数名：

- `SharedMarketRuntime._fallback_local`
- `_resolve_account_current_install`

当前逻辑：

会写回。`_fallback_local()` 本地采集成功后调用 `snapshot_upsert(... source_status="ok")`。前端 current-install 接口使用操作者账号本地采集成功后，如果存在 `shared_group_id`，也会调用 `contracts.snapshot_upsert()` 写入共享快照。

是否符合整改报告：

不符合。整改报告明确要求 worker 或操作者账号本地回退数据只服务当前 worker，不允许写回全局共享池。

证据位置：

- `backend/app/engine/shared_market_runtime.py:892-898`
- `backend/app/api/lottery.py:391-397`

风险判断：

某个操作者账号的异常、慢数据或错误数据可能污染共享池，影响其他操作者前端和 worker。

后续修改建议：

共享池只允许共享采集器和经过授权的共享检测链路写入；worker/操作者本地回退结果只能保存在当前运行上下文或单账号缓存，不写全局共享快照。

## 7. 结算是否阻塞 worker 主循环

文件路径：

- `backend/app/engine/worker.py`

函数名：

- `AccountWorker._main_loop`
- `AccountWorker._fetch_settlement_data`
- `AccountWorker._recover_unsettled_orders`

当前逻辑：

结算在 `_main_loop()` 中串行执行。下注窗口执行完后，worker 等待开奖倒计时归零，再等待 `settlement_wait_seconds`，默认 30 秒；之后 `_fetch_settlement_data()` 最多 6 次、每次间隔 5 秒拉取数据；拿到数据后再保存开奖结果、结算、反馈策略、对账。上述步骤全部 `await` 完成后，才进入下一轮主循环。

是否符合整改报告：

不符合。整改报告要求开奖数据获取和结算数据获取是独立分支，结算失败不能阻塞下一期下注；结算首取应为开奖归零后 20 秒，退避为 10/5/8/之后每 10 秒，最长等待 10 分钟。

证据位置：

- `backend/app/engine/worker.py:67-73`
- `backend/app/engine/worker.py:830-884`
- `backend/app/engine/worker.py:1089-1125`
- `backend/app/engine/worker.py:1127-1155`
- `backend/app/engine/worker.py:1216-1300`

风险判断：

结算数据延迟、接口异常或重登过程可能拖慢下一轮下注。当前 `_fetch_settlement_data()` 失败后会标记 `settle_failed`，不是 10 分钟内保持待结算。

后续修改建议：

将结算拆成账号维度后台分支或补偿队列；按账号串行聚合结算；10 分钟内保持 `settlement_pending`；结算失败不阻塞下一期下注。

## 8. 策略创建、编辑、启动、重启恢复是否依赖 verification_stale

文件路径：

- `backend/app/api/strategies.py`
- `backend/app/engine/manager.py`
- `backend/app/main.py`

函数名：

- `_validate_account_platform_gate_or_raise`
- `_validate_strategy_platform_type_for_account`
- `create_strategy`
- `update_strategy`
- `_transition_strategy`
- `_is_platform_verified_for_restore`
- `EngineManager.restore_workers_on_startup`

当前逻辑：

依赖。创建策略、编辑策略、启动策略都会通过账号验证门禁。只要 `verification_stale` 为真，就抛出错误，要求重新验证。服务启动恢复 worker 时也会检查 `verification_stale`，不通过就跳过恢复 worker。

是否符合整改报告：

不符合。整改报告希望创建策略不再由绑定账号验证状态主导，而是跟随 worker 登录链路和 worker 自身会话状态。

证据位置：

- `backend/app/api/strategies.py:255-296`
- `backend/app/api/strategies.py:464-488`
- `backend/app/api/strategies.py:544-579`
- `backend/app/api/strategies.py:780-804`
- `backend/app/engine/manager.py:106-113`
- `backend/app/engine/manager.py:471-480`
- `backend/app/main.py:63-75`

风险判断：

账号实际在线、worker 可重登时，策略仍可能因为 verification stale 被阻止创建、编辑、启动或重启恢复。这会造成“前端看到余额/验证过，但策略不能恢复运行”的体验问题。

后续修改建议：

保留账号绑定后的能力信息作为参考，不再作为策略创建、编辑、启动、恢复的硬阻断；真正执行由 worker session runtime 登录、心跳、重登和下注结果反馈决定。

## 9. 账号被顶后的自动重登逻辑

文件路径：

- `backend/app/engine/adapters/jnd.py`
- `backend/app/engine/session_runtime.py`
- `backend/app/engine/session.py`
- `backend/app/engine/worker.py`

函数名：

- `JndAdapter.get_current_install`
- `AccountSessionRuntime.run_platform_call`
- `AccountSessionRuntime.recover`
- `SessionManager._heartbeat_loop`
- `SessionManager.recover_after_api_failure`
- `AccountWorker._fetch_install_with_retry`
- `AccountWorker._recover_remote_login`

当前逻辑：

已存在自动恢复链路。平台 `GetCurrentInstall` 返回负状态时抛出 `RemoteLoginRequired`；`session_runtime.run_platform_call()` 捕获后执行 `recover()` 并重试一次平台调用；`recover()` 会先走 `SessionManager.recover_after_api_failure()`，该方法先执行心跳，心跳失败再重连。后台心跳每 10 秒一次，连续 3 次失败后自动重连。遇到风险、验证码、403 等阻断类错误时，会阻断自动重试 5 分钟。

是否符合整改报告：

部分符合。自动重登、心跳、请求串行已经存在；但结算链路仍未独立，部分失败仍会导致 `settle_failed` 或 worker 停止。

证据位置：

- `backend/app/engine/adapters/jnd.py:340-367`
- `backend/app/engine/session_runtime.py:172-187`
- `backend/app/engine/session_runtime.py:189-214`
- `backend/app/engine/session_runtime.py:248-255`
- `backend/app/engine/session.py:24-25`
- `backend/app/engine/session.py:215-252`
- `backend/app/engine/session.py:302-319`
- `backend/app/engine/worker.py:1007-1024`
- `backend/app/engine/worker.py:1046-1083`

风险判断：

账号被顶时大多数平台调用可以自动重登，但如果重登被验证码、403、风控阻断，会进入阻断状态。当前阻断后的前端中文提示和日志编号还不完整。

后续修改建议：

保留单账号 session runtime 串行登录和请求锁；补齐所有失败场景的中文提示和日志编号；结算分支也必须完整使用 recover 后重拉机制。

## 10. 前端开奖倒计时归零后的刷新逻辑

文件路径：

- `frontend/src/hooks/useLotteryCountdown.ts`
- `frontend/src/api/lottery.ts`
- `backend/app/api/lottery.py`
- `frontend/src/types/api/lottery.ts`

函数名：

- `CountdownStore.start`
- `CountdownStore.fetchNow`
- `fetchCurrentInstall`
- `get_current_install`
- `normalizeMarketDataState`

当前逻辑：

前端每秒本地递减倒计时。开奖倒计时从大于 0 变成 0 时，等待 30 秒后请求 `/lottery/current-install`。如果拿到的数据仍是可用但 `open_countdown_sec <= 0`，继续 30 秒后请求。如果数据不可用或请求失败，5 秒后重试。当前前端状态枚举只有 `shared_hit`、`local_fallback`、`processing`。

是否符合整改报告：

不符合。整改报告要求前端根据后端 `draw_pending`、`draw_wait_retry`、`market_closed` 和 `next_draw_retry_at` 等状态刷新，不再固定等 30 秒。

证据位置：

- `frontend/src/hooks/useLotteryCountdown.ts:6-8`
- `frontend/src/hooks/useLotteryCountdown.ts:105-130`
- `frontend/src/hooks/useLotteryCountdown.ts:142-178`
- `frontend/src/api/lottery.ts:15-26`
- `backend/app/api/lottery.py:444-510`
- `frontend/src/types/api/lottery.ts:18-25`
- `frontend/src/types/api/lottery.ts:92-98`

风险判断：

移动端和 Web 端都可能在开奖后长期显示旧期号或等待开奖，无法准确区分“正在获取开奖数据”“共享异常”“停盘”。

后续修改建议：

扩展后端响应状态和前端类型；开奖归零后前端 10 秒首刷；`draw_pending` 时 5 秒请求本系统后端；`draw_wait_retry` 时按 `next_draw_retry_at` 或 10 秒请求；停盘时停止重复追问并展示停盘。

## 后续修改优先级建议

第一优先级：

1. 移除 worker 下注前平台倒计时重校验，改为本地快照新鲜度校验。
2. 禁止 worker/前端本地采集结果写回共享池。
3. 共享状态契约扩展为 `shared_ok`、`draw_pending`、`draw_wait_retry`、`market_closed`、`shared_error`、`shared_stale`。
4. 共享采集器实现开奖专项刷新节奏。
5. 结算从 worker 主循环拆出，10 分钟内保持待结算，不提前 `settle_failed`。

第二优先级：

1. 策略创建、编辑、启动、重启恢复不再硬依赖 `verification_stale`。
2. 前端按后端状态刷新开奖数据，替换固定 30 秒逻辑。
3. 补齐中文告警和日志编号。
4. 增加共享组单飞锁、快照版本号、停盘恢复规则和结算幂等保护。

## 验收建议

1. 共享账号正常时，worker 和前端不直接调用操作者账号采集开奖倒计时。
2. 共享快照过期时，worker 不立即本地采集，必须先进入明确共享状态。
3. worker 下注前不再调用第三方平台倒计时接口。
4. 本地回退采集结果不会写入 `shared_market_snapshots`。
5. 开奖归零后前端和共享采集器按新节奏刷新。
6. 结算延迟时下一期仍可下注。
7. `verification_stale` 不再阻止策略创建、编辑、启动和服务重启恢复。
