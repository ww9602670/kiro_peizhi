# 需求文档：账号赔率管理

## 简介

当前系统中，执行器（BetExecutor）在每次下注前实时调用 `adapter.load_odds(issue)` 获取赔率。由于赔率在获取和下注之间可能发生变化，平台返回 `succeed=5`（赔率已改变），导致持续下注失败。

本功能通过引入 `account_odds` 数据表，将赔率快照持久化到本地数据库，在操作者登录投注平台时获取并存储赔率，检测赔率变动并要求操作者确认，最终让执行器从本地读取已确认的赔率，从根本上消除赔率变动导致的下注失败。

## 术语表

- **Account_Odds_Table**：`account_odds` 数据表，存储每个账号的赔率快照
  - account_id：INTEGER，外键关联 gambling_accounts(id)
  - key_code：TEXT，玩法编码
  - odds_value：INTEGER，赔率缩放值（平台浮点赔率 × 1000 后取整，如 2.053 → 2053）。取值范围 1~99999（对应平台赔率 0.001~99.999）
  - confirmed：INTEGER，0=未确认，1=已确认。API 层映射为 boolean（0→false，1→true）
  - fetched_at：TEXT，赔率获取时间，格式 `YYYY-MM-DD HH:MM:SS`（UTC 时区，由 SQLite `datetime('now')` 生成，与现有系统 bet_orders.created_at 等字段格式一致）
  - confirmed_at：TEXT 或 NULL，赔率确认时间，格式同 fetched_at，未确认时为 NULL
- **Operator**：SaaS 系统中的操作者，拥有一个或多个博彩账号，通过 JWT 认证
- **Gambling_Account**：操作者绑定的投注平台账号，存储在 `gambling_accounts` 表中，通过 operator_id 关联操作者
- **BetExecutor**：下注执行器（`backend/app/engine/executor.py`），`_execute_inner` 方法按顺序执行：1.去重 → 2.风控 → 3.获取赔率 → 4.构建下注数据 → 5.模拟单处理 → 6.调用平台下注。本功能改造步骤 3
- **PlatformAdapter**：投注平台适配器接口，`load_odds(issue)` 返回 `dict[str, int]`（KeyCode → 赔率缩放值）
- **KeyCode**：投注玩法编码（如 DX1、DS3、HZ15 等），完整列表见 `backend/app/utils/key_code_map.py`
- **AlertService**：告警服务（`backend/app/engine/alert.py`），通过 `send()` 方法创建告警记录到 `alerts` 表
- **Odds_Schema**：赔率相关的 Pydantic v2 Schema 集合（`backend/app/schemas/odds.py`）：
  - OddsItem：单条赔率记录，字段 key_code(str)、odds_value(int)、confirmed(bool)、fetched_at(str)、confirmed_at(str|None)
  - OddsListResponse：赔率列表响应，字段 account_id(int)、items(list[OddsItem])、has_unconfirmed(bool)
  - OddsConfirmResponse：赔率确认响应，字段 confirmed_count(int)
- **ApiResponse**：统一响应信封（`backend/app/schemas/common.py`），格式 `{code: int, message: str, data: T | None}`。code=0 表示成功，HTTP status 200；业务错误通过 code 表达（如 4001），HTTP status 由 BizError 的 status_code 参数决定（4001 对应 HTTP 404）
- **时间戳格式**：本功能所有时间戳字段统一使用 `YYYY-MM-DD HH:MM:SS` 格式（UTC 时区），由 SQLite `datetime('now')` 生成，与现有系统保持一致。字符串中不包含 "UTC" 后缀

## 需求

### 需求 1：赔率数据持久化

**用户故事：** 作为操作者，我希望系统能存储我的博彩账号赔率快照，以便在下注时使用本地赔率而非实时获取。

#### 验收标准

1. THE Account_Odds_Table SHALL 为每个 Gambling_Account 和 KeyCode 组合存储一条赔率记录，包含 account_id（INTEGER FK）、key_code（TEXT）、odds_value（INTEGER 赔率缩放值，范围 1~99999）、confirmed（INTEGER 0 或 1）、fetched_at（TEXT `YYYY-MM-DD HH:MM:SS`）和 confirmed_at（TEXT `YYYY-MM-DD HH:MM:SS` 或 NULL）
2. THE Account_Odds_Table SHALL 对 (account_id, key_code) 组合施加 UNIQUE 约束，确保同一账号同一 KeyCode 只有一条记录
3. WHEN 赔率记录被更新时，THE Account_Odds_Table SHALL 使用 INSERT OR REPLACE 语义覆盖旧记录，同时显式设置 odds_value 为新值、fetched_at 为 `datetime('now')`、confirmed 为调用方指定值（0 或 1）、confirmed_at 为调用方指定值（confirmed=1 时由调用方传入 `datetime('now')`，confirmed=0 时传入 NULL）

### 需求 2：登录时获取赔率

**用户故事：** 作为操作者，我希望在手动登录投注平台时自动获取最新赔率，以便系统始终拥有最新的赔率数据。

#### 验收标准

1. WHEN 操作者通过 `POST /accounts/{id}/login` 成功登录时，THE System SHALL 在查询余额之后、关闭 adapter 之前，调用 PlatformAdapter 的 `get_current_install()` 获取当前期号，再调用 `load_odds(issue)` 获取该期赔率数据
2. WHEN 赔率数据获取成功时，THE System SHALL 调用赔率同步逻辑（`_sync_odds`），根据需求 3 的规则决定写入方式和是否生成告警
3. IF 赔率获取过程中发生异常（网络超时、平台错误等），THEN THE System SHALL 记录 warning 级别日志（包含 account_id 和异常信息），登录结果仍返回成功
4. IF `load_odds(issue)` 返回空 dict 时，THEN THE System SHALL 视为获取成功但无赔率数据，不调用 `_sync_odds`，不修改 Account_Odds_Table，记录 info 级别日志
5. IF `_sync_odds` 执行过程中 DB 写入失败，THEN THE System SHALL 记录 error 级别日志（包含 account_id 和异常信息），登录结果仍返回成功，不影响响应格式
6. THE `POST /accounts/{id}/login` 响应 SHALL 保持现有 `ApiResponse[AccountInfo]` 格式不变（code=0, HTTP 200），赔率获取结果不影响响应结构

### 需求 3：赔率变动检测与提醒

**用户故事：** 作为操作者，我希望在赔率发生变化时收到提醒，以便我能及时确认新赔率。

#### 验收标准

1. WHEN 账号首次获取赔率（Account_Odds_Table 中无该 account_id 的任何记录）时，THE System SHALL 将所有赔率记录以 confirmed=1、confirmed_at=`datetime('now')` 写入，不生成赔率变动告警
2. WHEN 非首次获取赔率且新获取的赔率与 Account_Odds_Table 中已存储的赔率存在差异（任一 KeyCode 的 odds_value 不同，或存在新增/删除的 KeyCode）时，THE System SHALL 以 confirmed=0、confirmed_at=NULL 对新获取集合中的每个 (key_code, odds_value) 执行 INSERT OR REPLACE 写入（仅 upsert 新集合中的 KeyCode，不删除旧集合中存在但新集合中不存在的 KeyCode 记录——这些旧记录保持原样不变），并通过 AlertService 发送一条 alert_type="odds_changed"、level="warning" 的告警
3. WHEN 赔率变动告警生成时，THE AlertService SHALL 在告警 detail 字段中以文本格式列出所有变动项（格式：每行一个变动项）：
   - 值变动：`KeyCode: old_value → new_value`（KeyCode 在新旧集合中都存在但 odds_value 不同）
   - 新增：`KeyCode: 无 → new_value`（KeyCode 仅在新集合中存在，旧集合中无此 KeyCode）
   - 删除：`KeyCode: old_value → 已删除`（KeyCode 仅在旧集合中存在，新集合中无此 KeyCode。注意：此处"已删除"仅指该 KeyCode 不再出现在平台返回的赔率中，DB 中的旧记录不做物理删除，保持原样）
4. WHEN 非首次获取赔率且新获取的赔率与已存储的赔率完全相同（所有 KeyCode 的 odds_value 均一致且 KeyCode 集合相同）时，THE System SHALL 不修改 Account_Odds_Table 中的任何记录，不生成告警
5. THE System SHALL 对同一 account_id 不做赔率变动告警去重，每次登录检测到变动都生成新告警

### 需求 4：赔率确认流程

**用户故事：** 作为操作者，我希望能通过 API 和前端界面确认赔率更新，以便系统知道我已知晓赔率变化。

#### 验收标准

1. WHEN 操作者调用 `POST /accounts/{account_id}/odds/confirm` 时，THE System SHALL 将该 account_id 下所有 confirmed=0 的赔率记录更新为 confirmed=1、confirmed_at=`datetime('now')`。该操作是幂等的：若无 confirmed=0 的记录，则不修改任何行
2. WHEN 确认操作与赔率同步（`_sync_odds`）并发执行时，THE System SHALL 依赖 SQLite 的串行写入特性（WAL 模式下写操作互斥）保证数据一致性：确认操作和同步操作按先后顺序执行，不会产生部分确认/部分未确认的中间状态
3. WHEN 确认操作完成时，THE System SHALL 返回 HTTP 200 + `ApiResponse[OddsConfirmResponse]`（code=0），其中 confirmed_count 为本次从 confirmed=0 更新为 confirmed=1 的记录行数（幂等调用时为 0）
4. THE System SHALL 提供 `GET /accounts/{account_id}/odds` 端点，返回 HTTP 200 + `ApiResponse[OddsListResponse]`（code=0），其中：
   - account_id：请求的账号 ID
   - items：该账号所有赔率记录的 OddsItem 列表，按 key_code 字母序排列
   - has_unconfirmed：布尔值，当 items 中存在任何 confirmed=false 的记录时为 true，否则为 false；items 为空时为 false
5. WHEN 返回赔率列表时，每条 OddsItem SHALL 包含：key_code（str）、odds_value（int 赔率缩放值）、confirmed（bool，DB 层 0→false/1→true）、fetched_at（str `YYYY-MM-DD HH:MM:SS`）、confirmed_at（str `YYYY-MM-DD HH:MM:SS` | null）
6. IF 操作者请求的 account_id 不存在或不属于当前操作者，THEN THE System SHALL 返回 HTTP 404 + `ApiResponse`（code=4001, message="账号不存在", data=null）
7. THE Frontend SHALL 在账号卡片中显示赔率确认状态（已确认/待确认/未获取），待确认状态下显示"确认赔率"按钮

### 需求 5：执行器读取本地赔率

**用户故事：** 作为系统架构师，我希望执行器从本地数据库读取赔率而非实时调用平台 API，以便消除赔率变动导致的下注失败。

#### 验收标准

1. WHEN BetExecutor 执行 `_execute_inner` 的赔率获取步骤（原步骤 3：`odds = await self.adapter.load_odds(install.issue)`）时，THE BetExecutor SHALL 改为调用 `odds_get_confirmed_map(db, account_id=self.account_id)` 从 Account_Odds_Table 读取赔率
2. WHEN `odds_get_confirmed_map` 返回 None 且 Account_Odds_Table 中该 account_id 存在记录时（表示有未确认赔率），THE BetExecutor SHALL 跳过本次所有下注信号的执行（return），并通过 AlertService 发送一条 alert_type="odds_unconfirmed"、level="warning"、title="请先确认赔率更新" 的告警
3. WHEN `odds_get_confirmed_map` 返回 None 且 Account_Odds_Table 中该 account_id 无任何记录时，THE BetExecutor SHALL 跳过本次所有下注信号的执行（return），并通过 AlertService 发送一条 alert_type="odds_missing"、level="warning"、title="请先登录获取赔率" 的告警
4. WHEN `odds_get_confirmed_map` 返回有效的 `dict[str, int]`（所有赔率均已确认）时，THE BetExecutor SHALL 使用该 dict 作为赔率数据（格式与原 `load_odds` 返回一致：KeyCode → 赔率缩放值），继续执行后续步骤 4~6

### 需求 6：赔率数据序列化

**用户故事：** 作为开发者，我希望赔率数据能通过 API 正确序列化和反序列化，以便前后端数据一致。

#### 验收标准

1. THE Odds_Schema（OddsItem）SHALL 将赔率数据序列化为 JSON 格式，字段映射为：key_code → string、odds_value → integer（赔率缩放值）、confirmed → boolean（DB 0/1 映射为 false/true）、fetched_at → string（`YYYY-MM-DD HH:MM:SS`）、confirmed_at → string | null
2. FOR ALL 有效的 OddsItem 实例（key_code 为非空字符串、odds_value 为 1~99999 范围内的整数、confirmed 为布尔值、fetched_at 为 `YYYY-MM-DD HH:MM:SS` 格式字符串），通过 `model_dump_json()` 序列化后再通过 `OddsItem.model_validate_json()` 反序列化 SHALL 产生与原始实例所有字段值完全相等的对象
