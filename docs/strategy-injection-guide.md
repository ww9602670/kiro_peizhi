# 策略注入开发指南

本文档描述如何为自动投注平台开发自定义投注策略。通过策略注入接口，开发者可以在不修改核心投注引擎代码的前提下，注册新的投注策略。

---

## 1. 接口规范

### 1.1 BaseStrategy 抽象基类

所有策略必须继承 `BaseStrategy`（位于 `backend/app/engine/strategies/base.py`），并实现以下方法：

| 方法 | 类型 | 说明 |
|------|------|------|
| `name() -> str` | 抽象方法（必须实现） | 返回策略的唯一名称标识 |
| `compute(ctx: StrategyContext) -> list[BetInstruction]` | 抽象方法（必须实现） | 根据上下文计算本期下注指令，返回空列表表示本期不投注 |
| `on_result(is_win: Optional[int], pnl: int) -> None` | 可选覆盖 | 结算回调，用于更新策略内部状态 |

```python
from abc import ABC, abstractmethod
from typing import Optional

class BaseStrategy(ABC):

    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        ...

    @abstractmethod
    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """计算本期下注指令。返回空列表表示本期不投注。"""
        ...

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        """
        结算回调。
        is_win: 1=中奖, 0=未中, -1=退款
        pnl: 盈亏金额（单位：分）
        子类可覆盖此方法更新内部状态。
        """
        pass
```

#### `on_result` 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `is_win` | `Optional[int]` | `1`=中奖，`0`=未中，`-1`=退款（JND2.0 盘口和值 13/14 特殊规则），`None`=未知 |
| `pnl` | `int` | 盈亏金额（单位：分）。正数=盈利，负数=亏损，`0`=退款 |

> **注意**：`on_result` 是同步方法。如果策略需要发送告警等异步操作，应将告警信息暂存到内部队列，由外部（StrategyRunner）异步发送。参见马丁策略的 `PendingAlert` + `flush_alerts()` 模式。

---

### 1.2 数据类

#### StrategyContext — 策略计算上下文

每次调用 `compute()` 时，引擎会传入当前上下文：

```python
@dataclass
class StrategyContext:
    current_issue: str                    # 当前期号
    history: list[LotteryResult]          # 最近 N 期开奖数据（按时间倒序）
    balance: int                          # 博彩账号余额（单位：分）
    strategy_state: dict = field(...)     # 策略自定义状态（如马丁级别等）
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `current_issue` | `str` | 当前待投注的期号 |
| `history` | `list[LotteryResult]` | 最近 N 期开奖历史，可用于趋势分析、热号统计等 |
| `balance` | `int` | 当前博彩账号余额，单位为分（1元=100分） |
| `strategy_state` | `dict` | 策略自定义状态字典，可存储任意键值对（如马丁级别、连续命中次数等） |

#### LotteryResult — 开奖结果

```python
@dataclass
class LotteryResult:
    issue: str          # 期号
    balls: list[int]    # 开奖号码，3 个球 [球1, 球2, 球3]，每个值 0-9
    sum_value: int      # 和值 = 球1 + 球2 + 球3，范围 0-27
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `issue` | `str` | 期号 |
| `balls` | `list[int]` | 三个球的开奖号码，如 `[3, 5, 8]` |
| `sum_value` | `int` | 和值，范围 0-27 |

#### BetInstruction — 下注指令

`compute()` 方法的返回值，每条指令对应一笔下注：

```python
@dataclass
class BetInstruction:
    key_code: str    # 玩法代码（如 "DX1" 表示买大）
    amount: int      # 下注金额（单位：分）
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `key_code` | `str` | 玩法代码，必须与博彩平台 API 的 KeyCode 一致 |
| `amount` | `int` | 下注金额，单位为分（1元=100分），必须 > 0 |


**常用玩法代码速查表**：

| 玩法 | KeyCode | 说明 |
|------|---------|------|
| 大 | `DX1` | 和值 ≥ 14 |
| 小 | `DX2` | 和值 ≤ 13 |
| 单 | `DS3` | 和值为奇数 |
| 双 | `DS4` | 和值为偶数 |
| 大单 | `ZH7` | 和值 ≥ 14 且奇数 |
| 大双 | `ZH8` | 和值 ≥ 14 且偶数 |
| 小单 | `ZH9` | 和值 ≤ 13 且奇数 |
| 小双 | `ZH10` | 和值 ≤ 13 且偶数 |
| 极大 | `JDX5` | 和值 22-27 |
| 极小 | `JDX6` | 和值 0-5 |
| 和值 N | `HZ{N+1}` | 猜具体和值，HZ1=和值0，HZ28=和值27 |
| 豹子 | `BZ4` | 三球相同 |
| 红波 | `SB1` | 色波红 |
| 绿波 | `SB2` | 色波绿 |
| 蓝波 | `SB3` | 色波蓝 |
| 龙 | `LHH_L` | 球1 > 球3 |
| 虎 | `LHH_H` | 球1 < 球3 |
| 和 | `LHH_HE` | 球1 = 球3 |

> 完整玩法代码映射见 `backend/app/utils/key_code_map.py`。

---

## 2. 策略注册机制

### 2.1 `@register_strategy` 装饰器

策略通过 `@register_strategy(name)` 装饰器注册到全局注册表。注册后，投注引擎可通过名称查找并实例化策略。

```python
from app.engine.strategies.registry import register_strategy

@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):
    ...
```

**注册规则**：
- `name` 必须全局唯一，重复注册同名策略会抛出 `ValueError`
- 装饰器在模块导入时自动执行注册
- 策略模块需要被导入才能生效（见下方部署说明）

### 2.2 注册表查询 API

```python
from app.engine.strategies.registry import get_strategy_class, list_strategies

# 获取策略类
cls = get_strategy_class("flat")  # 返回 FlatStrategyImpl 类
# 未找到时抛出 KeyError

# 列出所有已注册策略
names = list_strategies()  # 返回 ["flat", "martin", ...]
```

---

## 3. 完整示例：反向追踪策略

以下示例实现一个"反向追踪"策略：统计最近 N 期的大小出现频率，投注出现次数较少的一方。

### 3.1 策略代码

创建文件 `backend/app/engine/strategies/reverse_track.py`：

```python
"""反向追踪策略：投注最近 N 期中出现次数较少的大/小方向。

示例：最近 10 期中"大"出现 7 次、"小"出现 3 次 → 投注"小"。
"""

from typing import Optional

from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    StrategyContext,
)
from app.engine.strategies.registry import register_strategy


@register_strategy("reverse_track")
class ReverseTrackStrategy(BaseStrategy):
    """反向追踪策略。

    Args:
        base_amount: 每期下注金额（单位：分）
        lookback: 回看期数（默认 10）
        threshold: 偏差阈值，大/小出现次数差 ≥ 此值才投注（默认 3）
    """

    def __init__(
        self,
        base_amount: int,
        lookback: int = 10,
        threshold: int = 3,
    ) -> None:
        if base_amount <= 0:
            raise ValueError("base_amount 必须大于 0")
        if lookback < 1:
            raise ValueError("lookback 必须 ≥ 1")
        if threshold < 1:
            raise ValueError("threshold 必须 ≥ 1")

        self._base_amount = base_amount
        self._lookback = lookback
        self._threshold = threshold

    def name(self) -> str:
        return "reverse_track"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """分析历史数据，投注出现次数较少的方向。"""
        history = ctx.history[:self._lookback]

        if len(history) < self._lookback:
            # 历史数据不足，本期不投注
            return []

        big_count = sum(1 for r in history if r.sum_value >= 14)
        small_count = len(history) - big_count

        diff = abs(big_count - small_count)
        if diff < self._threshold:
            # 偏差不够大，本期不投注
            return []

        # 投注出现次数较少的方向
        if big_count < small_count:
            return [BetInstruction(key_code="DX1", amount=self._base_amount)]
        else:
            return [BetInstruction(key_code="DX2", amount=self._base_amount)]

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        """无状态策略，on_result 为空操作。"""
        pass
```

### 3.2 代码要点说明

1. 继承 `BaseStrategy` 并实现 `name()` 和 `compute()` 两个抽象方法
2. 使用 `@register_strategy("reverse_track")` 注册，名称全局唯一
3. `compute()` 接收 `StrategyContext`，通过 `ctx.history` 获取历史开奖数据
4. 返回 `list[BetInstruction]`，空列表表示本期不投注
5. 金额单位为分（1元=100分），`BetInstruction(amount=1000)` 表示 10 元
6. 无状态策略的 `on_result()` 可以不覆盖（基类默认为空操作）

---

## 4. 开发 → 注册 → 测试 → 部署流程

### 4.1 开发

1. 在 `backend/app/engine/strategies/` 目录下创建新的 Python 模块（snake_case 命名）
2. 继承 `BaseStrategy`，实现 `name()` 和 `compute()` 方法
3. 使用 `@register_strategy("策略名称")` 装饰器注册
4. 如果策略有状态（如马丁策略），覆盖 `on_result()` 方法更新内部状态

**目录结构**：

```
backend/app/engine/strategies/
├── __init__.py
├── base.py              # BaseStrategy ABC + 数据类
├── registry.py          # 注册表
├── flat.py              # 内置：平注策略
├── martin.py            # 内置：马丁策略
└── reverse_track.py     # 新增：你的自定义策略
```

### 4.2 注册

策略模块必须被 Python 导入才能触发 `@register_strategy` 装饰器执行注册。

在 `backend/app/engine/strategies/__init__.py` 中导入新策略模块：

```python
# backend/app/engine/strategies/__init__.py
# 导入所有策略模块，触发 @register_strategy 注册
import app.engine.strategies.flat       # noqa: F401
import app.engine.strategies.martin     # noqa: F401
import app.engine.strategies.reverse_track  # noqa: F401  ← 新增
```

或者在引擎启动时动态导入（适用于插件化场景）：

```python
import importlib
importlib.import_module("app.engine.strategies.reverse_track")
```

### 4.3 测试

为新策略编写 pytest 单元测试，覆盖以下场景：

```python
# backend/tests/test_reverse_track_strategy.py
import pytest
from app.engine.strategies.base import BetInstruction, LotteryResult, StrategyContext
from app.engine.strategies.reverse_track import ReverseTrackStrategy


def _make_ctx(history: list[LotteryResult], issue: str = "20240101001") -> StrategyContext:
    """构造测试用 StrategyContext。"""
    return StrategyContext(
        current_issue=issue,
        history=history,
        balance=100_000,  # 1000 元
        strategy_state={},
    )


def _result(sum_value: int) -> LotteryResult:
    """快速构造 LotteryResult（balls 不影响策略逻辑）。"""
    return LotteryResult(issue="test", balls=[0, 0, sum_value], sum_value=sum_value)


class TestReverseTrackStrategy:
    """反向追踪策略单元测试。"""

    def test_name(self):
        s = ReverseTrackStrategy(base_amount=1000)
        assert s.name() == "reverse_track"

    def test_history_insufficient_skip(self):
        """历史数据不足时不投注。"""
        s = ReverseTrackStrategy(base_amount=1000, lookback=10)
        ctx = _make_ctx(history=[_result(15)] * 5)  # 只有 5 期
        assert s.compute(ctx) == []

    def test_diff_below_threshold_skip(self):
        """大小偏差不够大时不投注。"""
        s = ReverseTrackStrategy(base_amount=1000, lookback=10, threshold=3)
        # 6 大 4 小，差值 2 < 阈值 3
        history = [_result(15)] * 6 + [_result(10)] * 4
        ctx = _make_ctx(history=history)
        assert s.compute(ctx) == []

    def test_bet_small_when_big_dominant(self):
        """大出现多时投注小。"""
        s = ReverseTrackStrategy(base_amount=1000, lookback=10, threshold=3)
        # 8 大 2 小
        history = [_result(15)] * 8 + [_result(10)] * 2
        ctx = _make_ctx(history=history)
        result = s.compute(ctx)
        assert result == [BetInstruction(key_code="DX2", amount=1000)]

    def test_bet_big_when_small_dominant(self):
        """小出现多时投注大。"""
        s = ReverseTrackStrategy(base_amount=1000, lookback=10, threshold=3)
        # 2 大 8 小
        history = [_result(15)] * 2 + [_result(10)] * 8
        ctx = _make_ctx(history=history)
        result = s.compute(ctx)
        assert result == [BetInstruction(key_code="DX1", amount=1000)]

    def test_on_result_noop(self):
        """on_result 无副作用。"""
        s = ReverseTrackStrategy(base_amount=1000)
        s.on_result(is_win=1, pnl=500)  # 不应抛异常
        s.on_result(is_win=0, pnl=-1000)
        s.on_result(is_win=-1, pnl=0)

    def test_invalid_params(self):
        """参数校验。"""
        with pytest.raises(ValueError):
            ReverseTrackStrategy(base_amount=0)
        with pytest.raises(ValueError):
            ReverseTrackStrategy(base_amount=1000, lookback=0)
        with pytest.raises(ValueError):
            ReverseTrackStrategy(base_amount=1000, threshold=0)
```

运行测试：

```bash
cd backend
python -m pytest tests/test_reverse_track_strategy.py -v
```

### 4.4 部署

1. 确认策略模块已放置在 `backend/app/engine/strategies/` 目录下
2. 确认 `__init__.py` 中已导入新模块
3. 重启后端服务：`uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload`
4. 验证注册成功：

```python
from app.engine.strategies.registry import list_strategies
print(list_strategies())
# 输出应包含: [..., "reverse_track"]
```

5. 通过策略管理 API 创建使用新策略的配置（需在 `StrategyCreate` schema 的 `type` 字段中支持新策略名称）

---

## 5. 有状态策略开发指南

如果策略需要维护内部状态（如马丁策略的序列级别），需要注意以下要点：

### 5.1 通过 `on_result()` 更新状态

```python
class MyStatefulStrategy(BaseStrategy):
    def __init__(self, ...):
        self._consecutive_wins = 0  # 内部状态

    def on_result(self, is_win, pnl):
        if is_win == 1:
            self._consecutive_wins += 1
        elif is_win == 0:
            self._consecutive_wins = 0
        # is_win == -1（退款）时不改变状态
```

### 5.2 退款处理

JND2.0 盘口在和值 13/14 时部分玩法会退款（`is_win=-1, pnl=0`）。退款期不算命中也不算未命中，策略状态应保持不变。

### 5.3 异步告警模式

`on_result()` 是同步方法，不能直接调用异步的 `AlertService.send()`。推荐使用待发送队列模式：

```python
from dataclasses import dataclass

@dataclass
class PendingAlert:
    alert_type: str
    title: str
    detail: str

class MyStrategy(BaseStrategy):
    def __init__(self, alert_service=None, operator_id=0, ...):
        self._alert_service = alert_service
        self._operator_id = operator_id
        self._pending_alerts: list[PendingAlert] = []

    def on_result(self, is_win, pnl):
        # 同步逻辑中将告警加入队列
        if some_condition:
            self._pending_alerts.append(PendingAlert(
                alert_type="my_alert",
                title="告警标题",
                detail="告警详情",
            ))

    async def flush_alerts(self):
        """由 StrategyRunner 在 on_result 后异步调用。"""
        if not self._alert_service or not self._pending_alerts:
            self._pending_alerts.clear()
            return
        for alert in self._pending_alerts:
            await self._alert_service.send(
                operator_id=self._operator_id,
                alert_type=alert.alert_type,
                title=alert.title,
                detail=alert.detail,
            )
        self._pending_alerts.clear()
```

---

## 6. 金额单位约定

平台内部所有金额统一使用整数分（`int`），避免浮点精度问题：

| 场景 | 单位 | 示例 |
|------|------|------|
| `BetInstruction.amount` | 分 | `1000` = 10 元 |
| `StrategyContext.balance` | 分 | `100000` = 1000 元 |
| `on_result(pnl=...)` | 分 | `500` = 盈利 5 元，`-1000` = 亏损 10 元 |
| API 层输入/输出 | 元 | API 接收 `10.00`，内部转为 `1000` 分 |

---

## 7. 内置策略参考

### 7.1 平注策略（flat）

位于 `backend/app/engine/strategies/flat.py`，每期固定金额 + 固定玩法，无状态变化。

- `compute()`: 为每个 key_code 返回固定金额的 `BetInstruction`
- `on_result()`: 空操作

### 7.2 马丁策略（martin）

位于 `backend/app/engine/strategies/martin.py`，自定义倍率序列追注。

- `compute()`: 返回 `base_amount × sequence[level]` 的下注指令
- `on_result()`:
  - `is_win=1`（中奖）→ 重置 level=0，清零轮次亏损
  - `is_win=0`（未中）→ level+1，累加轮次亏损；序列跑完则重置 + 发送 `martin_reset` 告警
  - `is_win=-1`（退款）→ level 不变，轮次亏损不变
- 使用 `PendingAlert` + `flush_alerts()` 异步告警模式
