# KeyError 根本原因分析

## 问题描述

用户通过 SaaS 平台启动策略时，出现 KeyError: "策略 'flat' 未注册"

## 错误堆栈

```
File "H:\\d\\bocai_web\\backend\\app\\engine\\strategies\\registry.py", line 36, in get_strategy_class
    raise KeyError(f"策略 '{name}' 未注册")
KeyError: "策略 'flat' 未注册"
```

## 根本原因

### 1. 代码修复已完成 ✅

`backend/app/engine/strategies/__init__.py` 文件已经正确修复：

```python
"""策略模块初始化。

导入所有策略实现，确保 @register_strategy 装饰器被执行。
"""

# 导入所有策略实现，触发装饰器注册
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategies.martin import MartinStrategyImpl

__all__ = ["FlatStrategyImpl", "MartinStrategyImpl"]
```

### 2. 测试脚本验证通过 ✅

运行 `backend/test_complete_verification.py` 所有测试通过：

```
✅ 通过 - 策略注册
✅ 通过 - 策略实例化
✅ 通过 - _build_strategy_runner
✅ 通过 - 数据库查询
✅ 通过 - 启动 Worker
```

这说明代码修复是正确的。

### 3. HTTP API 调用失败 ❌

但是通过 HTTP API 调用 `/api/v1/strategies/{id}/start` 仍然出现 KeyError。

## 问题定位

### 关键发现

1. **测试脚本成功** - 直接导入模块并调用函数可以成功
2. **HTTP API 失败** - 通过 HTTP 请求调用 API 端点失败

这说明：**后端服务器进程没有加载修复后的代码！**

### 原因分析

1. **多个后端进程** - 检查发现有多个进程监听 8888 端口：
   ```
   TCP    0.0.0.0:8888    LISTENING    37468
   TCP    0.0.0.0:8888    LISTENING    22064
   TCP    0.0.0.0:8888    LISTENING    33072
   TCP    0.0.0.0:8888    LISTENING    12824
   TCP    0.0.0.0:8888    LISTENING    7908
   ```

2. **uvicorn --reload 未生效** - 虽然启动时使用了 `--reload` 参数，但可能：
   - 进程已经僵死，无法自动重新加载
   - 文件监控失效
   - 多个进程导致混乱

3. **旧代码仍在运行** - HTTP API 请求被路由到旧的进程，该进程仍然使用空的 `__init__.py`

## 解决方案

### 方案 1: 重启后端服务器（推荐）

1. 停止所有后端进程
2. 清理僵尸进程
3. 重新启动后端服务器
4. 验证策略启动功能

### 方案 2: 强制重新加载模块

在 `app/main.py` 的 lifespan 中添加强制重新加载：

```python
import importlib
import app.engine.strategies

# 强制重新加载策略模块
importlib.reload(app.engine.strategies)
```

但这不是根本解决方案，因为问题在于进程管理。

## 验证步骤

1. 停止所有后端进程
2. 确认 8888 端口没有进程监听
3. 启动新的后端服务器
4. 运行 `test_api_direct.py` 验证 API 调用
5. 通过前端 UI 测试策略启动

## 当前状态

- ✅ 代码修复完成
- ✅ 测试脚本验证通过
- ❌ HTTP API 调用失败（后端进程未重启）
- 🔄 需要重启后端服务器

## 下一步

1. 重启后端服务器
2. 验证 HTTP API 调用
3. 完成完整的端到端测试
4. 更新测试报告
