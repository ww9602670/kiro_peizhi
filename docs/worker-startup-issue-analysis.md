# Worker 未启动问题分析与修复

## 问题描述

用户通过 SaaS 平台启动策略后，策略状态显示为"运行中"，但没有看到任何投注记录。

## 根本原因

### 1. API 端点缺少 Worker 管理逻辑

在 `backend/app/api/strategies.py` 中，`start_strategy` 端点的实现如下：

```python
@router.post("/strategies/{strategy_id}/start")
async def start_strategy(
    strategy_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """启动策略（stopped/paused → running）。"""
    info = await _transition_strategy(strategy_id, "running", operator, db)
    return ApiResponse[StrategyInfo](data=info)
```

`_transition_strategy` 函数只做了两件事：
1. 验证状态转移是否合法
2. 更新数据库中的策略状态

**关键问题**: 没有调用 `EngineManager.start_worker()` 来实际启动 Worker 进程！

### 2. Worker 只在应用启动时恢复

Worker 的启动逻辑只存在于 `EngineManager.restore_workers_on_startup()` 方法中，该方法在应用启动时被调用：

```python
# backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：初始化数据库 + 恢复活跃会话 + 启动引擎
    await init_db()
    db = await get_shared_db()
    await restore_sessions(db)

    alert_service = AlertService(db)
    engine = EngineManager(db=db, alert_service=alert_service)
    app.state.engine = engine
    restored = await engine.restore_workers_on_startup()  # ← 只在这里启动 Worker
    await engine.start_health_check(admin_operator_id=1)

    yield

    # 关闭时：graceful shutdown
    await engine.shutdown()
    await close_shared_db()
```

这意味着：
- 如果用户在应用运行期间通过 UI 启动策略，Worker 不会被创建
- 只有重启应用后，Worker 才会被恢复
- 这导致策略状态与实际运行状态不一致

## 修复方案

### 修改 `_transition_strategy` 函数

在 `backend/app/api/strategies.py` 中，修改 `_transition_strategy` 函数，添加 Worker 管理逻辑：

```python
async def _transition_strategy(
    strategy_id: int,
    target_status: str,
    operator: dict,
    db,
    request: Request,  # ← 添加 Request 参数以访问 app.state.engine
) -> StrategyInfo:
    """通用状态转移逻辑。"""
    existing = await strategy_get_by_id(
        db, strategy_id=strategy_id, operator_id=operator["id"]
    )
    if not existing:
        raise BizError(4001, "策略不存在", status_code=404)

    current = existing["status"]
    if not validate_state_transition(current, target_status):
        raise BizError(
            4003,
            f"策略状态转移非法: {current} → {target_status}",
            status_code=400,
        )

    row = await strategy_update_status(
        db,
        strategy_id=strategy_id,
        operator_id=operator["id"],
        status=target_status,
    )
    
    # ← 新增：获取 EngineManager
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        raise BizError(5001, "引擎管理器未初始化", status_code=500)
    
    # ← 新增：如果是启动策略，启动 Worker
    if target_status == "running":
        # 获取账号信息
        account = await account_get_by_id(
            db, account_id=existing["account_id"], operator_id=operator["id"]
        )
        if not account:
            raise BizError(4001, "关联的博彩账号不存在", status_code=404)
        
        # 检查账号是否在线
        if account["status"] != "online":
            raise BizError(4002, "博彩账号未登录，请先登录账号", status_code=400)
        
        # 获取该账号的所有 running 策略
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
        ]
        
        # 启动或更新 Worker
        await engine.start_worker(
            operator_id=operator["id"],
            account_id=account["id"],
            account_name=account["account_name"],
            password=account["password"],
            platform_type=account.get("platform_type", "JND28WEB"),
            strategies=running_strategies,
        )
    
    # ← 新增：如果是停止策略，检查是否需要停止 Worker
    elif target_status == "stopped":
        # 获取该账号的所有 running 策略
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
            and s.get("id") != strategy_id  # 排除当前正在停止的策略
        ]
        
        # 如果没有其他 running 策略，停止 Worker
        if not running_strategies:
            await engine.stop_worker(account_id=existing["account_id"])
    
    return _to_strategy_info(row)
```

### 更新所有调用点

需要在所有调用 `_transition_strategy` 的地方添加 `request` 参数：

```python
@router.post("/strategies/{strategy_id}/start")
async def start_strategy(
    strategy_id: int,
    request: Request,  # ← 添加
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """启动策略（stopped/paused → running）。"""
    info = await _transition_strategy(strategy_id, "running", operator, db, request)
    return ApiResponse[StrategyInfo](data=info)


@router.post("/strategies/{strategy_id}/pause")
async def pause_strategy(
    strategy_id: int,
    request: Request,  # ← 添加
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """暂停策略（running → paused）。"""
    info = await _transition_strategy(strategy_id, "paused", operator, db, request)
    return ApiResponse[StrategyInfo](data=info)


@router.post("/strategies/{strategy_id}/stop")
async def stop_strategy(
    strategy_id: int,
    request: Request,  # ← 添加
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """停止策略（running/paused/error → stopped）。"""
    info = await _transition_strategy(strategy_id, "stopped", operator, db, request)
    return ApiResponse[StrategyInfo](data=info)
```

## 当前状态

- ✅ 代码已修改
- ✅ KeyError 根本原因已找到并修复
- 🔄 准备进行完整测试

## KeyError 根本原因

错误信息：`KeyError: "策略 'flat' 未注册"`

**根本原因**：`backend/app/engine/strategies/__init__.py` 文件是空的，导致策略模块没有被导入，`@register_strategy` 装饰器没有执行，策略类没有注册到全局注册表中。

**修复方案**：在 `__init__.py` 中导入所有策略实现：

```python
"""策略模块初始化。

导入所有策略实现，确保 @register_strategy 装饰器被执行。
"""

# 导入所有策略实现，触发装饰器注册
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategies.martin import MartinStrategyImpl

__all__ = ["FlatStrategyImpl", "MartinStrategyImpl"]
```

## 下一步

1. ✅ 修复策略注册问题
2. ✅ 修复策略构造函数参数不匹配问题
3. ✅ 修复余额获取问题
4. 🔄 使用 Chrome MCP 进行完整的 SaaS 平台测试
   - 重新登录博彩账号（验证余额正确显示）
   - 验证策略是否正常运行
   - 等待并检查投注记录
   - 验证余额和盈亏更新
5. 记录测试过程中发现的所有问题

## 相关文件

- `backend/app/api/accounts.py` - 博彩账号管理 API（已修复余额获取）
- `backend/app/api/strategies.py` - 策略管理 API
- `backend/app/engine/manager.py` - Worker 管理器
- `backend/app/engine/session.py` - 会话管理器（包含真实登录逻辑）
- `backend/app/engine/adapters/jnd.py` - JND 平台适配器（包含余额查询）
- `backend/app/main.py` - 应用入口和 lifespan 管理
- `docs/saas-platform-test-report.md` - 测试报告


## 问题 2: 策略构造函数参数不匹配 ✅ 已修复

**错误症状**:
- 策略状态显示为"运行中"
- 但没有产生任何投注记录
- Worker 可能因为构造策略实例失败而无法正常工作

**根本原因**:
`EngineManager._build_strategy_runner()` 方法中构造策略实例时，参数名称与策略类的构造函数不匹配：

1. **FlatStrategyImpl** 期望参数：
   - `key_codes: list[str]` - 玩法代码列表
   - `base_amount: int` - 基础金额（分）

2. **MartinStrategyImpl** 期望参数：
   - `key_codes: list[str]` - 玩法代码列表
   - `base_amount: int` - 基础金额（分）
   - `sequence: list[int | float]` - 倍率序列

3. **但 `_build_strategy_runner` 传递的参数**：
   - `play_code: str` - 单个玩法代码字符串（错误！）
   - `base_amount: int` - 基础金额

**修复方案**:
```python
def _build_strategy_runner(self, strategy_data: dict[str, Any]) -> Optional[StrategyRunner]:
    """从策略数据构建 StrategyRunner。"""
    from app.engine.strategies.registry import get_strategy_class

    strategy_type = strategy_data.get("type", "flat")
    strategy_cls = get_strategy_class(strategy_type)
    if strategy_cls is None:
        logger.warning("未知策略类型｜type=%s", strategy_type)
        return None

    # 构建策略实例
    play_code = strategy_data.get("play_code", "DX1")
    base_amount = strategy_data.get("base_amount", 100)
    
    if strategy_type == "flat":
        # 平注策略：key_codes 参数是列表
        kwargs: dict[str, Any] = {
            "key_codes": [play_code],  # ← 修复：将字符串包装成列表
            "base_amount": base_amount,
        }
    elif strategy_type == "martin":
        # 马丁策略：key_codes 参数是列表，还需要 sequence
        kwargs: dict[str, Any] = {
            "key_codes": [play_code],  # ← 修复：将字符串包装成列表
            "base_amount": base_amount,
        }
        if strategy_data.get("martin_sequence"):
            seq_str = strategy_data["martin_sequence"]
            if isinstance(seq_str, str):
                kwargs["sequence"] = [float(x) for x in seq_str.split(",")]
            elif isinstance(seq_str, list):
                kwargs["sequence"] = seq_str
        else:
            # 马丁策略必须有序列
            logger.warning("马丁策略缺少 martin_sequence｜strategy_id=%s", strategy_data.get("id"))
            return None
    else:
        logger.warning("未知策略类型｜type=%s", strategy_type)
        return None

    strategy_instance = strategy_cls(**kwargs)
    # ... 后续代码
```

**影响范围**:
- 所有策略的 Worker 启动
- 策略实例构造
- 投注指令生成

**修复状态**: ✅ 已修复

**验证方法**:
- 后端服务器自动重新加载
- 重新启动策略
- 观察是否产生投注记录

## 问题 3: 余额未正确获取 ✅ 已修复

**问题描述**: 账号登录后余额显示为 0.00 元，应该显示 20000 元（试玩平台初始余额）

**根本原因**:
`manual_login` API 端点只是桩实现，只更新状态为 "online"，没有调用真实的平台 API 获取余额。

**修复方案**:
修改 `backend/app/api/accounts.py` 中的 `manual_login` 端点：

```python
@router.post("/accounts/{account_id}/login")
async def manual_login(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """手动触发登录。

    调用真实平台适配器登录并获取余额。
    """
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "账号不存在", status_code=404)

    # 创建平台适配器
    from app.engine.adapters.jnd import JNDAdapter
    adapter = JNDAdapter(platform_type=account.get("platform_type", "JND28WEB"))
    
    try:
        # 调用真实登录
        login_result = await adapter.login(account["account_name"], account["password"])
        
        if not login_result.success:
            raise BizError(4003, f"登录失败: {login_result.message}", status_code=400)
        
        # 查询余额
        balance_info = await adapter.query_balance()
        balance_cents = int(balance_info.balance * 100)  # 元 → 分
        
        # 更新数据库
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        row = await account_update(
            db,
            account_id=account_id,
            operator_id=operator["id"],
            status="online",
            balance=balance_cents,
            last_login_at=now,
            login_fail_count=0,
        )
        
        return ApiResponse[AccountInfo](data=_to_account_info(row))
        
    except BizError:
        raise
    except Exception as e:
        logger.exception("登录异常｜account_id=%d", account_id)
        raise BizError(5001, f"登录异常: {str(e)}", status_code=500)
    finally:
        # 关闭 adapter session
        await adapter.close()
```

**修复内容**:
1. 创建 JNDAdapter 实例
2. 调用 `adapter.login()` 进行真实登录
3. 调用 `adapter.query_balance()` 查询余额
4. 将余额（元）转换为分存储到数据库
5. 更新账号状态、余额、登录时间
6. 正确处理异常并关闭 adapter session

**影响范围**:
- 账号登录功能
- 余额显示
- Worker 初始化时的余额数据

**修复状态**: ✅ 已修复

**验证方法**:
- 后端服务器自动重新加载
- 重新点击"登录"按钮
- 观察余额是否正确显示为 20000 元


**验证结果**: ✅ 已验证
- 测试脚本成功验证了完整流程
- 数据库成功更新为 2000000 分（20000 元）
- 前端仪表盘显示：总余额 20000.00 元
- 前端账号页面显示：余额 20000.00 元
- 截图保存：`frontend/screenshots/balance-fixed-20000.png`

## 当前状态总结

### 已修复的问题 ✅

1. **策略未注册 KeyError** - `backend/app/engine/strategies/__init__.py` 已修复
2. **策略构造函数参数不匹配** - `backend/app/engine/manager.py` 已修复
3. **余额未正确获取** - `backend/app/api/accounts.py` 已修复并验证

### 待解决的问题 ⚠️

1. **策略启动 KeyError** - 后端服务器可能需要重启以加载修复后的代码
2. **Worker 投注记录** - 需要验证 Worker 是否正常产生投注记录

### 下一步行动

1. 重启后端服务器（确保所有修复的代码被加载）
2. 重新启动策略
3. 等待 3-5 分钟观察投注记录
4. 验证余额和盈亏更新
5. 完成完整的端到端测试
