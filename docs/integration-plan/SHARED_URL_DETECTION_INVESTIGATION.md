# 共享池 URL 误标 uncovered 调研报告

> 日期：2026-05-10  
> 范围：服务器 release `6a28b05_shared_collector_20260504_064834` 中的共享市场探测逻辑  
> 现象：ceshi118 操作员账号登录后，操作员端持续 SHARED-002 告警；管理员端"共享网址"页一直显示该 URL 处于 review_required，点"重新检测"也无法消除

---

## 1. 现场数据快照（服务器生产 DB）

```sql
-- shared_market_groups（已配置 1 个组）
id=1, group_key='jnd28:https://8783288200-bty.mm555.co'
collector_platform_type='JND282'
collector_account_name='ceshi11'
enabled=1, collector_health_state='ok'

-- shared_market_group_urls（URL → 组的绑定）
id=1, shared_group_id=1, normalized_url='https://8783288200-bty.mm555.co'  ← 已绑定！
id=2, shared_group_id=1, normalized_url='https://3270795891-bty.cc555.co'
id=6, shared_group_id=1, normalized_url='https://1949737919-bty.mm555.co'

-- shared_market_uncovered_urls（未覆盖 URL 队列）
id=37, normalized_url='https://8783288200-bty.mm555.co', hit_count=59  ← 同一个 URL！
status='review_required', detection_status='failed'
failure_reason='no_shared_group_matched'

-- gambling_accounts
id=44, account_name=ceshi118, game_type='JND28', platform_url='https://8783288200-bty.mm555.co'
```

**关键矛盾**：URL `https://8783288200-bty.mm555.co` 同时存在于：
- `shared_market_group_urls` 表（已绑定到 group 1）
- `shared_market_uncovered_urls` 表（标记为"未匹配到任何共享组"）

这两个表对同一个 URL 给出了完全相反的结论。

---

## 2. 代码调用链

### 入口（[worker.py / shared_market_runtime.py:1300](backend/app/engine/shared_market_runtime.py#L1300)）
当账号 ceshi118 启动会话或定期巡检时，会触发：
```python
discover_and_bind_uncovered_url(
    platform_type=ceshi118.platform_type,  # 'JND28WEB'
    platform_url=ceshi118.platform_url,    # 'https://8783288200-bty.mm555.co'
    account_id=44,
)
```

### 探测主流程（[shared_market_runtime.py:1114](backend/app/engine/shared_market_runtime.py#L1114)）
```python
async def discover_and_bind_uncovered_url(...):
    # 步骤1：标记/创建 uncovered_url 记录，hit_count + 1
    touch_row = await uncovered_url_touch(normalized_url=url, ...)

    # 步骤2：标记 detecting
    await uncovered_url_mark_detecting(row_id)

    # 步骤3：调用核心匹配逻辑
    discovered_group_id, install = await _discover_url_group_match(
        platform_type=platform_type,
        normalized_url=normalized_url,
    )

    # 步骤4：根据结果分支
    if discovered_group_id is None:
        # 标记 review_required
        await uncovered_url_mark_review_required(failure_reason="no_shared_group_matched")
        return None, None
    else:
        await uncovered_url_mark_matched(shared_group_id=discovered_group_id)
        await shared_market_group_url_add(...)  # 绑定 URL → group
```

### 核心匹配逻辑（[shared_market_runtime.py:1444](backend/app/engine/shared_market_runtime.py#L1444)）
```python
async def _discover_url_group_match(platform_type, normalized_url):
    groups = await shared_market_group_list(include_disabled=False)
    if not groups:
        return None, None

    detector_account = BOCAI_SHARED_DETECTOR_ACCOUNT
    detector_password = BOCAI_SHARED_DETECTOR_PASSWORD

    for row in groups:
        # ⚠️ 关键判断：仅基于 platform_type 字符串相等性筛选
        collector_platform_type = (row.get("collector_platform_type") or "").upper()
        if collector_platform_type != platform_type.upper():
            continue   # ← ceshi118='JND28WEB' vs group='JND282' 在此被跳过

        # 用 detector 账号探测（实际登录 + get_install）
        install = await _probe_with_shared_account(...)
        return group_id, install

    return None, None  # ← 走到这里，标记 no_shared_group_matched
```

---

## 3. 根因（精确定位）

### 根因 A — 探测逻辑**不查 `shared_market_group_urls` 既有绑定**
`_discover_url_group_match` 完全不参考"URL 是否已经绑定到某个组"这个事实。即使 `shared_market_group_urls` 表里这条 URL 已绑定到 group 1，每次走探测都重新做"platform_type 字符串相等性匹配 + 实测登录"，结果反复失败。

正确的设计应该是：
1. **先查 `shared_market_group_urls`**：如果该 URL 已绑定到某个 enabled 组 → 直接复用，不再做实测
2. 仅当未绑定时才做平台类型匹配 + detector 探测

### 根因 B — `platform_type` 严格相等比较把同一平台的不同协议变体判定为不同
- ceshi118 注册时 `game_type='JND28'`，被 worker 解析为 `platform_type='JND28WEB'`
- shared_group 1 配置的 `collector_platform_type='JND282'`
- 两者严格不相等 → group 被跳过

但实际上 JND28WEB 和 JND282 是同一物理平台的两种协议适配器（一个走 web 一个走 app endpoint）。它们的行情数据是**完全相同**的——这正是为什么 `shared_market_group_urls` 里的 3 个 URL 都能为同一个 collector 服务。

### 根因 C — uncovered_url 永远不被自动清理
即使 URL 后来被人工绑定（手动 INSERT `shared_market_group_urls`），uncovered_urls 表里的同名记录也不会被清理。每次 worker 巡检该 URL，hit_count 继续累加，状态再次回到 review_required。

---

## 4. 为什么操作员一直收到 SHARED-002 告警

`worker.py` 在每次下注前会：
1. 通过路由表（或 fallback 逻辑）找到 ceshi118 应该消费哪个共享组的快照
2. 检查该共享组的最新 snapshot 是否新鲜（< 35 秒）
3. 不新鲜则触发 SHARED-002

由于：
- `_discover_url_group_match` 永远返回 None
- ceshi118 的会话启动流程把 URL 标 review_required 后**不再尝试绑定**
- 所以 ceshi118 找不到任何可消费的共享组 → 没有有效快照 → 35 秒不更新 → 告警

附：服务器上 `shared_market_snapshots` 只有 1 条且 `fetched_at = 2026-05-09 14:20:15`（很久没刷新）——印证了 collector 也已停摆。

---

## 5. 修复方向

按优先级排序：

### 修复 P0：`_discover_url_group_match` 优先查既有绑定
在 `discover_and_bind_uncovered_url` 顶部，进入 detector 探测前先查 `shared_market_group_urls`：

```python
existing = await shared_market_group_url_exists(normalized_url=normalized_url)
if existing and existing.get("shared_group_id"):
    group_id = existing["shared_group_id"]
    # 验证组仍然 enabled
    group_row = await shared_market_group_get(shared_group_id=group_id)
    if group_row and group_row.get("enabled"):
        # 命中：直接标 matched，清理 uncovered_url，无需再 detector probe
        await uncovered_url_mark_matched(row_id, shared_group_id=group_id)
        return group_id, None  # install 不必要，让 worker 用 snapshot 而不是新 install
```

### 修复 P1：platform_type 等价匹配
建立一张 platform_type 的等价类映射，比如：
```python
PLATFORM_TYPE_EQUIV = {
    "JND28WEB": {"JND28WEB", "JND282"},
    "JND282":   {"JND28WEB", "JND282"},
}

# 替换 if collector_platform_type != normalized_platform_type:
allowed = PLATFORM_TYPE_EQUIV.get(normalized_platform_type, {normalized_platform_type})
if collector_platform_type not in allowed:
    continue
```
或者更激进：完全去掉 platform_type 严格相等检查，只信任 URL 已绑定的事实和 detector 探测结果。

### 修复 P2：uncovered_url 在被绑定后自动清理
当一个 URL 出现在 `shared_market_group_urls` 时，对应的 `shared_market_uncovered_urls` 行应该自动转为 `status='matched'`、`detection_status='matched'`，不再向 worker 报告"未覆盖"。

最简实现：在 `shared_market_group_url_add` 函数末尾追加：
```python
await db.execute(
    "UPDATE shared_market_uncovered_urls "
    "SET status='matched', detection_status='matched', "
    "    matched_shared_group_id=?, shared_group_id=?, last_checked_at=? "
    "WHERE normalized_url=?",
    (shared_group_id, shared_group_id, _now(), normalized_url),
)
```

### 修复 P3："重新检测"按钮真正调用 detection
管理员点"重新检测"应同步调用 `discover_and_bind_uncovered_url`，结合 P0 修复后能立即得到正确结果。

---

## 6. 服务器线上的 collector 当前状态额外说明

- `collector_account_name='ceshi11'`：**该账号已被软删除**（gambling_accounts.status='deleted'）
- `collector_last_success_at=2026-05-09 14:20:15`：最后一次成功采集是 1 天以前
- `collector_consecutive_error_count=0` 但 `collector_last_error='TimeoutError'`、`error_class='network_timeout'`
- 综合：**collector 是停摆的状态**——账号已删，登录用的密码 `Qq1122` 在平台上对应的账号已无效

**这是一个独立的运维问题**，不是代码 bug：需要：
1. 重建一个有效的 collector 账号（可以叫回 ceshi11/jiance11，但需要真实可登录的账号）
2. 把账号信息写回 `shared_market_groups.collector_account_name + collector_password_enc`
3. 重新启用该共享组（enabled=1）

---

## 7. 下一步行动

| 工作 | 位置 | 实施 |
|---|---|---|
| 修复 P0 | shared_market_runtime.py `discover_and_bind_uncovered_url` 头部加既有绑定查询 | B.2.4 一并实施 |
| 修复 P1 | shared_market_runtime.py `_discover_url_group_match` platform_type 比较改为等价类匹配 | B.2.4 一并实施 |
| 修复 P2 | db_ops.py `shared_market_group_url_add` 末尾自动清理 uncovered_url 记录 | B.2.4 一并实施 |
| 修复 P3 | api/admin.py recheck 端点改为同步调用 detection | B.2.4 主任务 |
| 运维任务 | 创建有效 collector 账号 + 更新 shared_market_groups | 阶段 E（部署侧），不在本 plan |
