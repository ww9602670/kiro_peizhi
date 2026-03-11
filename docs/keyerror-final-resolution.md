# KeyError 问题最终解决报告

**日期**: 2026-03-03  
**问题**: 策略启动时出现 KeyError: "策略 'flat' 未注册"  
**状态**: ✅ 已彻底解决

## 问题回顾

用户通过 SaaS 平台启动策略时，出现 KeyError 对话框，导致策略无法启动。

## 修复过程

### 1. 代码修复（已完成）✅

修复了三个关键问题：

#### 问题 1: 策略未注册
- **文件**: `backend/app/engine/strategies/__init__.py`
- **原因**: 文件为空，策略模块未被导入
- **修复**: 添加导入语句，触发 @register_strategy 装饰器

```python
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategies.martin import MartinStrategyImpl

__all__ = ["FlatStrategyImpl", "MartinStrategyImpl"]
```

#### 问题 2: 策略构造函数参数不匹配
- **文件**: `backend/app/engine/manager.py`
- **原因**: 传递 `play_code` (字符串)，但策略期望 `key_codes` (列表)
- **修复**: 将 `play_code` 包装成列表 `[play_code]`

```python
kwargs: dict[str, Any] = {
    "key_codes": [play_code],  # ← 修复
    "base_amount": base_amount,
}
```

#### 问题 3: 余额未正确获取
- **文件**: `backend/app/api/accounts.py`
- **原因**: `manual_login` 只是桩实现，未调用真实 API
- **修复**: 调用 JNDAdapter 进行真实登录和余额查询

### 2. 验证测试（已完成）✅

#### 测试 1: 代码层面验证
运行 `backend/test_complete_verification.py`：

```
✅ 通过 - 策略注册
✅ 通过 - 策略实例化
✅ 通过 - _build_strategy_runner
✅ 通过 - 数据库查询
✅ 通过 - 启动 Worker
```

#### 测试 2: HTTP API 验证（初次失败）
运行 `backend/test_api_direct.py`：

```
❌ 启动策略失败: HTTP 500
KeyError: "策略 'flat' 未注册"
```

**原因**: 后端服务器进程未重新加载修复后的代码

### 3. 根本原因定位 ✅

发现有多个进程监听 8888 端口：
```
TCP    0.0.0.0:8888    LISTENING    37468
TCP    0.0.0.0:8888    LISTENING    22064
TCP    0.0.0.0:8888    LISTENING    33072
TCP    0.0.0.0:8888    LISTENING    12824
TCP    0.0.0.0:8888    LISTENING    7908
```

这些是僵尸进程或旧进程，虽然使用了 `uvicorn --reload`，但自动重新加载失效。

### 4. 最终解决方案 ✅

1. **停止所有后端进程**
   ```powershell
   Get-Process | Where-Object {$_.ProcessName -like "*python*" -or $_.ProcessName -like "*uvicorn*"} | Stop-Process -Force
   ```

2. **确认端口清空**
   ```powershell
   netstat -ano | findstr ":8888" | findstr "LISTENING"
   # 无输出，确认端口已释放
   ```

3. **重新启动后端服务器**
   ```powershell
   cd backend
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
   ```

4. **验证 HTTP API**
   ```
   ✅ API 测试通过！
   ```

5. **验证前端 UI**
   - ✅ 登录成功
   - ✅ 余额显示：20000.00 元
   - ✅ 策略停止成功
   - ✅ 策略启动成功（无 KeyError）
   - ✅ 状态变为"运行中"

## 测试结果

### HTTP API 测试 ✅

```
============================================================
测试通过 HTTP API 启动策略
============================================================

1. 登录获取 token...
✅ 登录成功

2. 查询策略列表...
✅ 查询成功，共 1 个策略

3. 停止策略 173...
✅ 策略停止成功

4. 启动策略 173...
   HTTP 状态码: 200
✅ 策略启动成功！
   状态: running

============================================================
✅ API 测试通过！
============================================================
```

### 前端 UI 测试 ✅

1. ✅ 登录成功（page_op_0 / pass123456）
2. ✅ 仪表盘显示正确
   - 总余额：20000.00 元
   - 运行中策略：1 个
3. ✅ 策略页面显示正确
   - 策略名：测试平注策略
   - 类型：平注
   - 状态：运行中
4. ✅ 策略停止成功
   - 状态变为"已停止"
   - 按钮变为"启动/编辑/删除"
5. ✅ 策略启动成功（关键测试）
   - **无 KeyError 对话框**
   - 状态变为"运行中"
   - 按钮变为"暂停/停止"

### 截图证据

- `frontend/screenshots/strategy-start-success-fixed.png` - 策略启动成功截图

## 总结

### 问题根源

1. **代码层面**: 三个关键 bug（策略注册、参数不匹配、余额获取）
2. **运行时层面**: 后端服务器进程未重新加载修复后的代码

### 解决方案

1. **代码修复**: 修复三个 bug
2. **进程管理**: 重启后端服务器

### 验证结果

✅ **所有测试通过**
- 代码层面测试通过
- HTTP API 测试通过
- 前端 UI 测试通过
- 策略启动功能完全正常

## 经验教训

1. **uvicorn --reload 不总是可靠**: 在某些情况下（多进程、僵尸进程），自动重新加载可能失效
2. **需要验证进程状态**: 修改代码后，应确认后端进程已重新加载
3. **多层验证**: 代码测试通过不代表 HTTP API 就能工作，需要端到端验证

## 后续建议

1. **监控进程状态**: 定期检查是否有僵尸进程
2. **使用进程管理工具**: 考虑使用 supervisor 或 systemd 管理后端进程
3. **添加健康检查**: 在 API 中添加版本号或构建时间，方便确认代码是否已更新

## 状态

🎉 **KeyError 问题已彻底解决！策略启动功能完全正常！**
