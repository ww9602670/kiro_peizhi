# Session Token 修复文档

## 问题描述

策略启动后，Worker 正常运行并收集到下注信号，但所有下注都被风控拒绝，错误信息：

```
风控拒绝｜check=session｜strategy_id=173｜reason=会话无效：无 session_token
```

## 根本原因

`SessionManager.login()` 成功后设置了 `self.session_token`（内存中），但**没有将 token 更新到数据库**。

而 `RiskController._check_session()` 是从数据库读取 `session_token` 的：

```python
# backend/app/engine/risk.py
async def _check_session(self, signal: BetSignal) -> RiskCheckResult:
    account = await account_get_by_id(
        self.db, account_id=self.account_id, operator_id=self.operator_id
    )
    if not account:
        return RiskCheckResult(passed=False, reason="博彩账号不存在")
    token = account.get("session_token")  # 从数据库读取
    if not token:
        return RiskCheckResult(passed=False, reason="会话无效：无 session_token")
    return RiskCheckResult(passed=True)
```

## 修复方案

### 1. 添加数据库更新方法

在 `SessionManager` 中添加 `_update_session_token_to_db()` 方法：

```python
async def _update_session_token_to_db(self) -> None:
    """将 session_token 更新到数据库。"""
    try:
        await account_update(
            self.db,
            account_id=self.account_id,
            operator_id=self.operator_id,
            session_token=self.session_token,
        )
        logger.info(
            "✅ session_token 已更新到数据库｜account_id=%d token=%s",
            self.account_id,
            self.session_token[:20] + "..." if self.session_token else "None",
        )
    except Exception as e:
        logger.error(
            "❌ 更新 session_token 到数据库失败｜account_id=%d error=%s",
            self.account_id,
            e,
        )
```

### 2. 在登录成功后调用

修改 `SessionManager.login()` 方法，在登录成功后立即更新数据库：

```python
if result.success:
    self.session_token = result.token
    self.login_fail_count = 0
    self.captcha_fail_count = 0
    self._login_error = False
    
    # 🔑 关键修复：将 session_token 更新到数据库
    await self._update_session_token_to_db()
    
    self._start_heartbeat()
    logger.info(...)
    return True
```

### 3. 在重连时也更新

修改 `SessionManager._reconnect()` 方法，在 token 刷新成功后也更新数据库：

```python
if new_token:
    self.session_token = new_token
    await self._update_session_token_to_db()  # 添加此行
    self._start_heartbeat()
    logger.info("账号 %d token 刷新成功", self.account_id)
    return
```

### 4. 添加 db 参数

修改 `SessionManager.__init__()` 添加 `db` 参数：

```python
def __init__(
    self,
    *,
    adapter: PlatformAdapter,
    alert_service: AlertService,
    captcha_service: CaptchaService,
    operator_id: int,
    account_id: int,
    account_name: str,
    password: str,
    db: aiosqlite.Connection,  # 新增
    on_status_change: Optional[Callable[[int, str], Awaitable[None]]] = None,
) -> None:
    # ...
    self.db = db  # 新增
```

### 5. 更新 EngineManager

修改 `EngineManager.start_worker()` 在创建 `SessionManager` 时传递 `db`：

```python
session = SessionManager(
    adapter=adapter,
    alert_service=self.alert_service,
    captcha_service=captcha_service,
    operator_id=operator_id,
    account_id=account_id,
    account_name=account_name,
    password=password,
    db=self.db,  # 添加此行
)
```

### 6. 更新测试文件

修改所有测试文件中创建 `SessionManager` 的地方，添加 `db` 参数：

- `backend/tests/test_session.py` (2 处)
- `backend/tests/test_engine_integration.py` (3 处)

## 修改文件清单

1. `backend/app/engine/session.py`
   - 添加 `import aiosqlite` 和 `from app.models.db_ops import account_update`
   - 添加 `db` 参数到 `__init__()`
   - 添加 `_update_session_token_to_db()` 方法
   - 在 `login()` 成功后调用 `_update_session_token_to_db()`
   - 在 `_reconnect()` token 刷新成功后调用 `_update_session_token_to_db()`

2. `backend/app/engine/manager.py`
   - 在 `start_worker()` 创建 `SessionManager` 时添加 `db=self.db`

3. `backend/tests/test_session.py`
   - 在 `session` fixture 添加 `db` 参数
   - 在 `test_5_failures_calls_on_status_change` 添加 `db` 参数

4. `backend/tests/test_engine_integration.py`
   - 在 3 处 `SessionManager` 创建时添加 `db=db`

## 验证步骤

1. 停止所有后端进程
2. 重新启动后端服务器
3. 启动策略
4. 检查后端日志，应该看到：
   ```
   ✅ Worker 登录成功，进入主循环｜operator_id=1 account_id=363
   ✅ session_token 已更新到数据库｜account_id=363 token=...
   📊 收集到下注信号｜issue=... signals_count=1 account_id=363
   ```
5. 等待 2-3 分钟，检查是否产生投注记录
6. 不应再看到 "风控拒绝｜check=session" 错误

## 测试脚本

运行 `backend/test_session_token_fix.py` 验证修复：

```bash
cd backend
python test_session_token_fix.py
```

预期输出：
```
✅ 登录成功
✅ 修复成功！session_token 已正确更新到数据库
```

## 影响范围

- 所有使用 `SessionManager` 的地方都需要传递 `db` 参数
- 风控检查现在可以正确读取到 session_token
- Worker 可以正常通过风控检查并执行下注

## 后续优化建议

1. 考虑在 `SessionManager` 中缓存数据库连接，避免每次都传递
2. 考虑使用事件机制通知数据库更新，而不是直接调用
3. 添加 session_token 过期检测和自动刷新机制
