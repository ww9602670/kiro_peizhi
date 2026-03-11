# 实施计划：账号赔率管理

## 概述

将赔率管理从"每次下注实时获取"改为"登录时获取 → 本地存储 → 变动检测 → 操作者确认 → 执行器读取本地"的流程。按契约先行原则，先定义 Schema/类型，再实现后端 CRUD/API，最后改造登录流程和执行器，前端同步跟进。

## 任务

- [x] 1. 数据库层：account_odds 表
  - [x] 1.1 在 `backend/app/database.py` 的 `DDL_STATEMENTS` 列表末尾追加 account_odds 表 DDL（第 9 张表）
    - CREATE TABLE IF NOT EXISTS account_odds（id, account_id FK, key_code, odds_value, confirmed, fetched_at, confirmed_at）
    - UNIQUE(account_id, key_code) 约束
    - CREATE INDEX idx_account_odds_account ON account_odds(account_id, confirmed)
    - DoD：DDL 追加后 init_db() 成功；表结构含 7 个字段 + UNIQUE 约束 + 索引；ON DELETE CASCADE 生效
    - _需求: 1.1, 1.2_

  - [ ]* 1.2 pytest 单元测试：验证 account_odds 表创建、UNIQUE 约束（重复 (account_id, key_code) 插入 → IntegrityError）、ON DELETE CASCADE（删除 gambling_accounts 行后 account_odds 关联行自动删除）
    - DoD：3 个测试用例全部通过（表创建、UNIQUE 约束、CASCADE 删除）
    - _需求: 1.1, 1.2_

- [x] 2. CRUD 层：赔率操作函数
  - [x] 2.1 在 `backend/app/models/db_ops.py` 中实现 `odds_batch_upsert(db, *, account_id, odds_map, confirmed)`
    - INSERT OR REPLACE 语义，事务内批量执行，全部成功或全部回滚
    - fetched_at = datetime('now')，confirmed 转 0/1，confirmed_at 按 confirmed 决定
    - 空 odds_map 时为 no-op（不执行任何 SQL）
    - DoD：非空 odds_map 写入后 SELECT 验证记录数 = len(odds_map)；空 odds_map 不抛异常
    - _需求: 1.3, 2.2_

  - [x] 2.2 在 `backend/app/models/db_ops.py` 中实现 `odds_list_by_account(db, *, account_id)` → list[dict]
    - SELECT * FROM account_odds WHERE account_id=? ORDER BY key_code ASC
    - DoD：写入 3 条不同 key_code 后返回列表长度=3 且按字母序排列
    - _需求: 4.4, 4.5_

  - [x] 2.3 在 `backend/app/models/db_ops.py` 中实现 `odds_get_confirmed_map(db, *, account_id)` → dict | None
    - 无记录 → None；存在 confirmed=0 → None；全部 confirmed=1 → {key_code: odds_value}
    - DoD：三种场景各一个断言验证返回值类型和内容
    - _需求: 5.1, 5.2, 5.3, 5.4_

  - [x] 2.4 在 `backend/app/models/db_ops.py` 中实现 `odds_confirm_all(db, *, account_id)` → int
    - UPDATE confirmed=1, confirmed_at=datetime('now') WHERE account_id=? AND confirmed=0
    - 返回更新行数，幂等（无 confirmed=0 则返回 0）
    - DoD：写入 N 条 confirmed=0 后调用返回 N；二次调用返回 0
    - _需求: 4.1, 4.2, 4.3_

  - [x] 2.5 在 `backend/app/models/db_ops.py` 中实现 `odds_has_records(db, *, account_id)` → bool
    - SELECT COUNT(*) FROM account_odds WHERE account_id=?
    - DoD：空表返回 False；写入后返回 True
    - _需求: 5.2, 5.3_

  - [ ]* 2.6 pytest 单元测试 `backend/tests/test_odds.py`（CRUD 部分）
    - odds_batch_upsert 基本写入 + 覆盖更新（同 key_code 新 odds_value）
    - odds_list_by_account 返回按 key_code 字母序排列
    - odds_get_confirmed_map 三种场景（无记录/有未确认/全确认）
    - odds_confirm_all 确认 + 幂等（二次调用返回 0）
    - odds_has_records 有/无记录
    - 边界：空 odds_map（no-op）、单条赔率、大量赔率（80+ KeyCode）
    - DoD：所有用例通过，覆盖 5 个 CRUD 函数 × 正常 + 边界场景
    - _需求: 1.1, 1.2, 1.3, 4.1, 4.2, 5.1, 5.2, 5.3, 5.4_

  - [ ]* 2.7 hypothesis 属性测试 `backend/tests/test_odds_properties.py`（P1, P2, P6, P7, P8）
    - **Property 1: UPSERT 幂等性** — *For any* (account_id, key_code) 组合和任意序列写入，始终只有一条记录且 odds_value 等于最后一次写入值
    - **Validates: Requirements 1.1, 1.2, 1.3**
    - **Property 2: 批量写入完整性** — *For any* 非空 odds_map，upsert(confirmed=False) 后记录数=键数量，confirmed=0，confirmed_at=NULL
    - **Validates: Requirements 2.2, 2.4**
    - **Property 6: 批量确认** — *For any* 含 N 条 confirmed=0 的账号，confirm_all 后全部 confirmed=1，返回值=N
    - **Validates: Requirements 4.1**
    - **Property 7: 未确认赔率阻断下注** — *For any* 含至少一条 confirmed=0 的 account_id，get_confirmed_map 返回 None
    - **Validates: Requirements 5.2**
    - **Property 8: 赔率读取往返一致性** — *For any* 有效 odds_map，以 confirmed=True 写入后 get_confirmed_map 返回的 dict 与原始 odds_map 完全相等
    - **Validates: Requirements 5.4**
    - 每个属性 @settings(max_examples=100)，使用 :memory: 数据库
    - DoD：5 个属性测试全部通过（各 100 次迭代）
    - _需求: 1.1, 1.2, 1.3, 4.1, 5.2, 5.4_

- [x] 3. Checkpoint — 数据库层和 CRUD 层验证
  - 运行 `pytest backend/tests/test_odds.py backend/tests/test_odds_properties.py -v`
  - 通过条件：所有测试通过；CRUD 5 个函数均有测试覆盖；属性测试 P1/P2/P6/P7/P8 各 100 次迭代通过
  - 如有问题请询问用户

- [x] 4. Schema 层 + API 层（契约先行）
  - [x] 4.1 创建 `backend/app/schemas/odds.py`：OddsItem、OddsListResponse、OddsConfirmResponse
    - Pydantic v2 写法（model_config = ConfigDict(...)）
    - OddsItem: key_code(str), odds_value(int, ge=1, le=99999), confirmed(bool), fetched_at(str), confirmed_at(str|None)
    - OddsListResponse: account_id(int), items(list[OddsItem]), has_unconfirmed(bool)
    - OddsConfirmResponse: confirmed_count(int)
    - 含 json_schema_extra 示例
    - DoD：三个 Schema 类定义完成；OddsItem 字段验证（odds_value 超范围 → ValidationError）
    - _需求: 6.1_

  - [x] 4.2 创建 `backend/app/api/odds.py`：赔率 API 路由
    - GET /accounts/{account_id}/odds → ApiResponse[OddsListResponse]
    - POST /accounts/{account_id}/odds/confirm → ApiResponse[OddsConfirmResponse]
    - 两个端点均需 JWT 认证（get_current_operator）+ 账号归属校验（account_get_by_id + operator_id）
    - 账号不存在或不属于当前操作者 → BizError(4001, "账号不存在", status_code=404)
    - router 内只定义相对路径，遵循 proxy-only 约定
    - DoD：两个端点可通过 TestClient 调用；认证 + 归属校验 + 信封格式正确
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 4.3 在 `backend/app/main.py` 注册 odds_router（prefix="/api/v1", tags=["odds"]）
    - DoD：`/api/v1/accounts/{id}/odds` 路由可达
    - _需求: 4.4_

  - [ ]* 4.4 pytest 单元测试 `backend/tests/test_odds.py`（API 部分，追加到同一文件）
    - GET /odds 成功返回 code=0 + OddsListResponse（items 按 key_code 字母序）
    - POST /odds/confirm 成功返回 code=0 + OddsConfirmResponse（含幂等验证：二次调用 confirmed_count=0）
    - 账号不存在 → code=4001, HTTP 404
    - 账号不属于当前操作者 → code=4001, HTTP 404
    - 未认证 → HTTP 401
    - DoD：5 个测试场景全部通过；响应均为统一信封格式
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 4.5 hypothesis 属性测试 `backend/tests/test_odds_properties.py`（P9，追加到同一文件）
    - **Property 9: OddsItem 序列化往返** — *For any* 有效 OddsItem 实例，model_dump_json() → OddsItem.model_validate_json() 所有字段值完全相等
    - **Validates: Requirements 6.1, 6.2**
    - 生成器：key_code=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('L','N'))), odds_value=st.integers(1, 99999), confirmed=st.booleans(), fetched_at=时间戳字符串
    - DoD：属性测试 100 次迭代通过
    - _需求: 6.1, 6.2_

- [x] 5. Checkpoint — Schema 和 API 层验证
  - 运行 `pytest backend/tests/test_odds.py backend/tests/test_odds_properties.py -v`
  - 通过条件：所有测试通过；API 端点返回统一信封格式；P9 序列化往返 100 次通过
  - 如有问题请询问用户

- [x] 6. 登录流程改造：赔率同步
  - [x] 6.1 在 `backend/app/api/accounts.py` 中实现 `_sync_odds(db, account_id, operator_id, new_odds)` 函数
    - 首次获取（无记录）→ confirmed=True 写入，不告警
    - 非首次 + 有变动（odds_value 不同、新增/删除 KeyCode）→ confirmed=False 全量写入 + odds_changed 告警
    - 告警 detail 格式：`KeyCode: old_value → new_value`（新增：`KeyCode: 无 → new_value`，删除：`KeyCode: old_value → 已删除`），每行一个变动项
    - 非首次 + 无变动 → 不修改，不告警
    - DoD：三种场景各有断言验证（confirmed 状态 + 告警生成/不生成 + detail 格式）
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 6.2 在 `backend/app/api/accounts.py` 的 `manual_login` 端点中，查询余额之后、adapter.close() 之前，增加赔率获取逻辑
    - try: get_current_install() → load_odds(issue) → 非空时调用 _sync_odds()
    - load_odds 返回空 dict → 记录 info 日志，不调用 _sync_odds
    - 赔率获取异常 → logger.warning("赔率获取失败 account_id=%d: %s", account_id, e)，不阻断登录
    - _sync_odds DB 写入异常 → logger.error(...)，不阻断登录
    - 登录响应保持 ApiResponse[AccountInfo] 格式不变
    - DoD：登录成功后 account_odds 表有记录（mock adapter）；异常时登录仍返回 code=0；空 dict 时不调用 _sync_odds
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ]* 6.3 pytest 单元测试 `backend/tests/test_odds.py`（_sync_odds 部分，追加到同一文件）
    - 首次获取：confirmed=1，无 odds_changed 告警
    - 非首次 + 变动：confirmed=0，生成 odds_changed 告警，detail 含变动 KeyCode（含新增/删除格式验证）
    - 非首次 + 无变动：记录不变，无告警
    - 赔率获取异常：登录仍成功，warning 日志
    - load_odds 返回空 dict：不调用 _sync_odds，info 日志
    - _sync_odds DB 写入失败：登录仍成功，error 日志
    - DoD：6 个测试场景全部通过
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 6.4 hypothesis 属性测试 `backend/tests/test_odds_properties.py`（P3, P4, P5, P10，追加到同一文件）
    - **Property 3: 赔率变动检测与告警** — *For any* 两组不同 odds_map，先写入 old_odds(confirmed=True)，再 _sync_odds(new_odds)，应：(a) 全部 confirmed=0，(b) 生成 odds_changed 告警，(c) detail 含变动 key_code
    - **Validates: Requirements 3.1, 3.2, 3.3**
    - **Property 4: 相同赔率幂等性** — *For any* odds_map，先写入并确认，再 _sync_odds 传入相同 map，confirmed 保持 1，无新告警
    - **Validates: Requirements 3.4**
    - **Property 5: 首次获取自动确认** — *For any* 非空 odds_map 和无记录的 account_id，_sync_odds 后 confirmed=1，confirmed_at 非空，无 odds_changed 告警
    - **Validates: Requirements 3.1**
    - **Property 10: 告警 detail 包含所有变动 KeyCode** — *For any* 两组不同 odds_map，_sync_odds 后告警 detail 中每个变动 KeyCode 都出现，格式正确（含新增/删除）
    - **Validates: Requirements 3.3**
    - DoD：4 个属性测试全部通过（各 100 次迭代）
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 7. 执行器改造：读取本地赔率
  - [x] 7.1 修改 `backend/app/engine/executor.py` 的 `_execute_inner` 步骤 3
    - 替换 `odds = await self.adapter.load_odds(install.issue)` 为 `odds = await odds_get_confirmed_map(self.db, account_id=self.account_id)`
    - odds is None + 无记录 → AlertService.send(alert_type="odds_missing", title="请先登录获取赔率"), return
    - odds is None + 有记录 → AlertService.send(alert_type="odds_unconfirmed", title="请先确认赔率更新"), return
    - odds 有效 → 继续步骤 4~6（格式与原 load_odds 返回一致：KeyCode → 赔率缩放值）
    - DoD：mock DB 返回三种状态，验证下注执行/跳过行为 + AlertService.send 调用参数
    - _需求: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 7.2 pytest 单元测试 `backend/tests/test_odds.py`（执行器部分，追加到同一文件）
    - 全部已确认 → 正常下注（odds dict 传递正确）
    - 有未确认 → 跳过下注 + odds_unconfirmed 告警（mock AlertService.send 验证 alert_type 参数）
    - 无记录 → 跳过下注 + odds_missing 告警（mock AlertService.send 验证 alert_type 参数）
    - DoD：3 个测试场景全部通过；AlertService.send 调用参数断言正确
    - _需求: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 7.3 hypothesis 属性测试 `backend/tests/test_odds_properties.py`（P11，追加到同一文件）
    - **Property 11: 执行器根据赔率状态正确触发告警** — *For any* account_id，get_confirmed_map 返回 None 时：has_records=True → odds_unconfirmed 告警；has_records=False → odds_missing 告警；两种情况均跳过下注
    - **Validates: Requirements 5.2, 5.3**
    - DoD：属性测试 100 次迭代通过
    - _需求: 5.2, 5.3_

- [x] 8. Checkpoint — 后端所有赔率相关测试验证
  - 运行 `pytest backend/tests/test_odds.py backend/tests/test_odds_properties.py -v`
  - 通过条件：所有测试通过；11 个属性测试（P1~P11）各 100 次迭代通过；单元测试覆盖 CRUD/API/_sync_odds/执行器全部场景
  - 如有问题请询问用户

- [x] 9. 前端契约类型 + API 封装（成对提交）
  - [x] 9.1 创建 `frontend/src/types/api/odds.ts`：OddsItem、OddsListResponse、OddsConfirmResponse 接口
    - 与后端 Pydantic schema 一一对应（字段名、类型、可选性完全匹配）
    - DoD：TypeScript 编译通过；字段与 backend/app/schemas/odds.py 一一对应
    - _需求: 6.1_

  - [x] 9.2 创建 `frontend/src/api/odds.ts`：getAccountOdds(accountId) + confirmAccountOdds(accountId)
    - 通过统一请求层 @/api/request，使用相对路径（proxy-only）
    - DoD：API 函数导出正确；请求路径为 `/accounts/${accountId}/odds` 和 `/accounts/${accountId}/odds/confirm`
    - _需求: 4.4, 4.6_

  - [ ]* 9.3 vitest 单元测试 `frontend/tests/unit/odds-api.test.ts`
    - API 封装函数调用路径正确性
    - 错误处理（ApiError 捕获）
    - DoD：测试通过；验证 request 函数被正确调用
    - _需求: 4.4, 4.6_

- [x] 10. 前端 UI：账号卡片赔率状态
  - [x] 10.1 修改 `frontend/src/pages/operator/Accounts.tsx`：在 AccountCard 中增加赔率状态显示
    - 登录后调用 getAccountOdds 获取赔率状态
    - 显示赔率状态 badge：已确认（绿色）/ 待确认（橙色）/ 未获取（灰色）
    - 待确认状态下显示"确认赔率"按钮
    - 点击确认后调用 confirmAccountOdds，成功后刷新赔率状态
    - 错误处理：catch ApiError 并 alert(err.message)
    - DoD：三种状态 badge 正确显示；确认按钮点击后状态刷新；错误时 alert 提示
    - _需求: 4.7_

- [x] 11. 最终 Checkpoint — 前后端所有测试验证
  - 运行后端：`pytest backend/tests/test_odds.py backend/tests/test_odds_properties.py -v`
  - 运行前端：`pnpm --filter frontend test --run`
  - 通过条件：后端全部通过（单元 + 属性）；前端全部通过；前后端契约类型一致
  - 如有问题请询问用户

## 备注

- 标记 `*` 的子任务为可选（测试类），可跳过以加速 MVP
- 每个任务包含 DoD（Definition of Done）明确完成标准
- 每个任务引用具体需求编号以确保可追溯
- Checkpoint 包含明确的通过条件
- 属性测试验证通用正确性属性（11 个 Property），单元测试验证具体示例和边界条件
- 后端属性测试使用 hypothesis，前端属性测试使用 fast-check（本功能无前端属性测试需求）
- 所有前端 API 调用遵循 proxy-only 约定（相对路径 `/api/v1`）
