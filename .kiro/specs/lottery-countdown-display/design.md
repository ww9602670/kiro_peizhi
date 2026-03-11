# 彩票倒计时显示与下注时机优化 - 设计文档

## 1. 架构设计

### 1.1 系统架构
```
前端 (React)
  ├─ Dashboard 组件
  │   └─ CountdownDisplay 组件
  ├─ Strategy 组件
  │   └─ CountdownDisplay 组件
  └─ API 调用层
      └─ lottery.ts

后端 (FastAPI)
  ├─ API 层
  │   └─ /api/v1/lottery/current-install
  ├─ Engine 层
  │   ├─ Worker (使用 State 判断)
  │   └─ Poller (轮询期号信息)
  └─ Adapter 层
      └─ JNDAdapter (调用平台 API)
```

### 1.2 数据流
```
平台 API (GetCurrentInstall)
  ↓
JNDAdapter.get_current_install()
  ↓
API Endpoint (/api/v1/lottery/current-install)
  ↓
前端 API 调用 (fetchCurrentInstall)
  ↓
React 组件 (CountdownDisplay)
  ↓
实时倒计时显示
```

## 2. 后端设计

### 2.1 API Schema

#### 2.1.1 状态枚举定义
```python
# backend/app/schemas/lottery.py
from enum import IntEnum

class LotteryState(IntEnum):
    """彩票状态枚举"""
    OPEN = 1      # 开盘中，可下注
    CLOSED = 2    # 封盘中，不可下注
    DRAWING = 3   # 开奖中，不可下注
    UNKNOWN = 0   # 未知状态，不可下注
```

#### 2.1.2 响应 Schema
```python
# backend/app/schemas/lottery.py
from pydantic import BaseModel, Field, ConfigDict

class CurrentInstallResponse(BaseModel):
    """当前期号信息响应"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "installments": "3403606",
                "state": 1,
                "close_countdown_sec": 149,
                "open_countdown_sec": 159,
                "pre_lottery_result": "0,3,0",
                "pre_installments": "3403605",
                "template_code": "JNDPCDD"
            }
        }
    )
    
    installments: str = Field(..., description="当前期号")
    state: int = Field(..., description="状态：1=开盘，2=封盘，3=开奖，0=未知")
    close_countdown_sec: int = Field(..., description="封盘倒计时（剩余秒数）", ge=0)
    open_countdown_sec: int = Field(..., description="开奖倒计时（剩余秒数）", ge=0)
    pre_lottery_result: str = Field(..., description="上期开奖结果")
    pre_installments: str = Field(..., description="上期期号")
    template_code: str = Field(..., description="模板代码")
```

#### 2.1.3 API 端点
```python
# backend/app/api/lottery.py
from fastapi import APIRouter, Depends
from app.schemas.lottery import CurrentInstallResponse
from app.schemas.common import Envelope

router = APIRouter()

@router.get("/current-install")
async def get_current_install(
    platform_type: str = "JND28WEB"
) -> Envelope[CurrentInstallResponse]:
    """获取当前期号信息（含倒计时）
    
    返回统一信封格式：
    {
        "code": 0,
        "message": "success",
        "data": { ... }
    }
    """
    # 实现逻辑
    pass
```

### 2.2 Adapter 改进

#### 2.2.1 JNDAdapter 新增方法
```python
# backend/app/engine/adapters/jnd.py
async def get_current_install_detail(self) -> dict:
    """获取当前期号详细信息（含倒计时）
    
    注意：复用现有的 authenticated aiohttp session，不创建新的 httpx client
    
    Returns:
        {
            "installments": "3403606",
            "state": 1,  # 1=开盘，2=封盘，3=开奖，0=未知（归一化）
            "close_countdown_sec": 149,  # 剩余秒数
            "open_countdown_sec": 159,   # 剩余秒数
            "pre_lottery_result": "0,3,0",
            "pre_installments": "3403605",
            "template_code": "JNDPCDD"
        }
    """
    url = f"{self.base_url}/PlaceBet/GetCurrentInstall?lotteryType={self.lottery_type}"
    
    # 复用现有的 authenticated session
    data = await self._post(url)
    
    # 未知状态归一化为 0
    raw_state = data.get("State", 0)
    normalized_state = raw_state if raw_state in [1, 2, 3] else 0
    
    return {
        "installments": str(data.get("Installments", "")),
        "state": normalized_state,  # 归一化后的状态
        "close_countdown_sec": int(data.get("CloseTimeStamp", 0)),
        "open_countdown_sec": int(data.get("OpenTimeStamp", 0)),
        "pre_lottery_result": str(data.get("PreLotteryResult", "")),
        "pre_installments": str(data.get("PreInstallments", "")),
        "template_code": str(data.get("TemplateCode", "")),
    }
```

#### 2.2.2 更新 InstallInfo 数据类
```python
# backend/app/engine/adapters/base.py
@dataclass
class InstallInfo:
    """当前期号信息。"""
    issue: str                # 期号
    state: int                # 1=开盘, 2=封盘, 3=开奖, 0=未知
    close_countdown_sec: int  # 封盘倒计时（剩余秒数）
    pre_issue: str            # 上期期号
    pre_result: str           # 上期开奖结果 "b1,b2,b3"
    is_new_issue: bool = False  # 是否新期号
    open_countdown_sec: int = 0   # 距开奖秒数（剩余秒数）
    
    # 兼容性：保留旧字段名作为属性
    @property
    def close_timestamp(self) -> int:
        """兼容旧代码：close_timestamp → close_countdown_sec"""
        return self.close_countdown_sec
    
    @property
    def open_timestamp(self) -> int:
        """兼容旧代码：open_timestamp → open_countdown_sec"""
        return self.open_countdown_sec
```

#### 2.2.3 更新 BetResult 数据类
```python
# backend/app/engine/adapters/base.py
@dataclass
class BetResult:
    """下注结果。"""
    succeed: int              # 1=成功, 0=失败
    message: str              # 错误消息
    raw_response: dict = field(default_factory=dict)
    
    @property
    def error_code(self) -> str:
        """从 message 或 raw_response 提取错误码
        
        平台错误消息映射：
        - "赔率已经改变" → ODDS_CHANGED
        - "已经关盘" / "已经封盘" → CLOSED
        - "期号不匹配" / "期号已变化" → INSTALLMENTS_MISMATCH
        - 其他 → UNKNOWN
        """
        msg = self.message.lower()
        if "赔率" in msg and "改变" in msg:
            return "ODDS_CHANGED"
        if "关盘" in msg or "封盘" in msg:
            return "CLOSED"
        if "期号" in msg:
            return "INSTALLMENTS_MISMATCH"
        return "UNKNOWN"
    
    @property
    def is_retryable(self) -> bool:
        """判断错误是否可重试
        
        不可重试：ODDS_CHANGED, CLOSED, INSTALLMENTS_MISMATCH
        可重试：网络超时等其他错误
        """
        return self.error_code not in ["ODDS_CHANGED", "CLOSED", "INSTALLMENTS_MISMATCH"]
```

### 2.3 Worker 改进

#### 2.3.1 使用 State 判断下注时机（含条件提交策略）
```python
# backend/app/engine/worker.py
def _should_bet(self, install: InstallInfo) -> bool:
    """判断是否应该下注（第一次校验）
    
    规则：
    - State != 1 → 不可下注（封盘或开奖中）
    - close_countdown_sec <= 18s → 跳过（时间不足）
    - State == 1 且 close_countdown_sec > 18s → 可以下注
    """
    # 首先检查 State
    if install.state != 1:
        logger.info(
            "跳过：非开盘状态｜issue=%s state=%d account_id=%d",
            install.issue,
            install.state,
            self.account_id,
        )
        return False
    
    # 再检查剩余时间
    remaining = install.close_countdown_sec
    if remaining <= SKIP_THRESHOLD:
        logger.info(
            "跳过：剩余时间不足｜issue=%s remaining=%ds threshold=%ds account_id=%d",
            install.issue,
            remaining,
            SKIP_THRESHOLD,
            self.account_id,
        )
        return False
    
    return True

async def _place_bet_with_validation(self, install: InstallInfo, bet_plan: BetPlan) -> bool:
    """下注前二次校验（条件提交策略）
    
    在提交下注请求前，再次获取最新状态进行校验，
    确保期号一致、状态正确、时间充足。
    
    注意：这不是真正的原子性保证（需要平台接口支持条件提交），
    而是"条件提交+幂等+失败重试"策略。
    """
    # 二次获取最新状态
    latest_install = await self.adapter.get_current_install_detail()
    
    # 关键：校验期号一致性（防止跨期下注）
    if latest_install["installments"] != install.issue:
        logger.warning(
            "下注前二次校验失败：期号已变化｜expected=%s actual=%s account_id=%d",
            install.issue,
            latest_install["installments"],
            self.account_id,
        )
        return False
    
    # 校验状态仍为开盘
    if latest_install["state"] != 1:
        logger.warning(
            "下注前二次校验失败：状态已变化｜issue=%s state=%d account_id=%d",
            install.issue,
            latest_install["state"],
            self.account_id,
        )
        return False
    
    # 校验剩余时间充足
    if latest_install["close_countdown_sec"] <= SKIP_THRESHOLD:
        logger.warning(
            "下注前二次校验失败：时间不足｜issue=%s remaining=%ds account_id=%d",
            install.issue,
            latest_install["close_countdown_sec"],
            self.account_id,
        )
        return False
    
    # 执行下注（绑定期号参数）
    return await self._execute_bet_with_installments(
        install, 
        bet_plan, 
        expected_installments=install.issue
    )

async def _execute_bet_with_installments(
    self, 
    install: InstallInfo, 
    bet_plan: BetPlan,
    expected_installments: str
) -> bool:
    """执行下注（绑定期号参数）
    
    Args:
        install: 期号信息
        bet_plan: 下注计划
        expected_installments: 期望的期号（用于平台接口校验）
    
    Returns:
        下注是否成功
        
    注意：
    - 使用现有的 place_bet(issue, betdata) 接口
    - issue 参数即为期号绑定
    - 平台会自动校验期号是否匹配
    """
    try:
        # 调用现有的 place_bet 接口，issue 参数即为期号绑定
        result = await self.adapter.place_bet(
            issue=expected_installments,  # 绑定期号
            betdata=bet_plan.items,
        )
        
        # 处理结果
        if result.succeed == 1:
            return True
        else:
            # 根据错误类型决定是否重试
            if not result.is_retryable:
                # 不可重试的错误（ODDS_CHANGED, CLOSED, INSTALLMENTS_MISMATCH）
                logger.warning(
                    "下注失败（不可重试）｜issue=%s error_code=%s message=%s account_id=%d",
                    install.issue,
                    result.error_code,
                    result.message,
                    self.account_id,
                )
                return False
            else:
                # 可重试的错误（如网络超时）
                # 注意：根据主 spec 的"Confirmbet 零重试"原则，
                # 这里不实现重试逻辑，仅记录日志
                logger.info(
                    "下注失败（可重试但不重试）｜issue=%s error_code=%s message=%s account_id=%d",
                    install.issue,
                    result.error_code,
                    result.message,
                    self.account_id,
                )
                return False
    except Exception as e:
        logger.error(
            "下注异常｜issue=%s error=%s account_id=%d",
            install.issue,
            str(e),
            self.account_id,
        )
        return False
```

## 3. 前端设计

### 3.1 TypeScript 类型定义

```typescript
// frontend/src/types/api/lottery.ts

/**
 * 彩票状态枚举
 */
export enum LotteryStateEnum {
  OPEN = 1,      // 开盘中
  CLOSED = 2,    // 封盘中
  DRAWING = 3,   // 开奖中
  UNKNOWN = 0    // 未知状态
}

/**
 * 当前期号信息
 */
export interface CurrentInstall {
  installments: string;           // 当前期号
  state: LotteryStateEnum;        // 状态：1=开盘，2=封盘，3=开奖，0=未知
  close_countdown_sec: number;    // 封盘倒计时（剩余秒数）
  open_countdown_sec: number;     // 开奖倒计时（剩余秒数）
  pre_lottery_result: string;     // 上期开奖结果
  pre_installments: string;       // 上期期号
  template_code: string;          // 模板代码
}

/**
 * 状态显示配置
 */
export interface LotteryStateDisplay {
  label: string;    // 状态文字：开盘中/封盘中/开奖中/未知
  color: string;    // 颜色：green/red/yellow/gray
}

/**
 * 状态映射表
 */
export const STATE_DISPLAY_MAP: Record<LotteryStateEnum, LotteryStateDisplay> = {
  [LotteryStateEnum.OPEN]: { label: '开盘中', color: 'green' },
  [LotteryStateEnum.CLOSED]: { label: '封盘中', color: 'red' },
  [LotteryStateEnum.DRAWING]: { label: '开奖中', color: 'yellow' },
  [LotteryStateEnum.UNKNOWN]: { label: '未知', color: 'gray' },
};
```

### 3.2 API 调用层

```typescript
// frontend/src/api/lottery.ts
import { request } from '@/api/request';
import type { CurrentInstall } from '@/types/api/lottery';

/**
 * 获取当前期号信息（含倒计时）
 * 
 * 注意：后端返回 Envelope 格式，request 层会自动解包 data 字段
 */
export function fetchCurrentInstall(platformType: string = 'JND28WEB') {
  return request<CurrentInstall>('/lottery/current-install', {
    params: { platform_type: platformType }
  });
}
```

### 3.3 倒计时组件（使用共享 Hook）

```typescript
// frontend/src/hooks/useLotteryCountdown.ts
import { useState, useEffect } from 'react';
import { fetchCurrentInstall } from '@/api/lottery';
import type { CurrentInstall } from '@/types/api/lottery';

/**
 * 共享的彩票倒计时 Hook
 * 
 * 注意：建议配合 React Query 或 SWR 使用，确保全局单一轮询源。
 * 当前实现为简化版本，多组件使用时会产生多个轮询实例。
 * 
 * 生产环境建议：
 * - 使用 React Query: useQuery('lottery-countdown', fetchCurrentInstall, { refetchInterval: 5000 })
 * - 或使用全局 store（Zustand/Redux）+ 单一轮询源
 */
export function useLotteryCountdown() {
  const [data, setData] = useState<CurrentInstall | null>(null);
  const [closeCountdown, setCloseCountdown] = useState(0);
  const [openCountdown, setOpenCountdown] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdateTime, setLastUpdateTime] = useState<Date | null>(null);
  
  // 每 5 秒获取最新数据
  useEffect(() => {
    const fetchData = async () => {
      try {
        const result = await fetchCurrentInstall();
        setData(result);
        setCloseCountdown(result.close_countdown_sec);
        setOpenCountdown(result.open_countdown_sec);
        setError(null);
        setLastUpdateTime(new Date());
      } catch (err) {
        console.error('获取期号信息失败:', err);
        setError('数据延迟');
      }
    };
    
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);
  
  // 每秒更新倒计时（钳制为非负值）
  useEffect(() => {
    const timer = setInterval(() => {
      setCloseCountdown(prev => Math.max(0, prev - 1));
      setOpenCountdown(prev => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  
  return {
    data,
    closeCountdown,
    openCountdown,
    error,
    lastUpdateTime,
  };
}
```

```typescript
// frontend/src/components/CountdownDisplay.tsx
import { useLotteryCountdown } from '@/hooks/useLotteryCountdown';
import { STATE_DISPLAY_MAP, LotteryStateEnum } from '@/types/api/lottery';
import './CountdownDisplay.css';

export function CountdownDisplay() {
  const { data, closeCountdown, openCountdown, error, lastUpdateTime } = useLotteryCountdown();
  
  const getStateDisplay = () => {
    if (!data) return STATE_DISPLAY_MAP[LotteryStateEnum.UNKNOWN];
    return STATE_DISPLAY_MAP[data.state] || STATE_DISPLAY_MAP[LotteryStateEnum.UNKNOWN];
  };
  
  const stateDisplay = getStateDisplay();
  
  return (
    <div className="countdown-display">
      {error && (
        <div className="error-banner">
          {error} {lastUpdateTime && `(最后更新: ${lastUpdateTime.toLocaleTimeString()})`}
        </div>
      )}
      <div className="current-issue">
        <span>当前期号：</span>
        <strong>{data?.installments || '-'}</strong>
      </div>
      <div className={`state state-${stateDisplay.color}`}>
        {stateDisplay.label}
      </div>
      <div className="countdown">
        <div>
          <span>封盘倒计时：</span>
          <strong>{closeCountdown}秒</strong>
        </div>
        <div>
          <span>开奖倒计时：</span>
          <strong>{openCountdown}秒</strong>
        </div>
      </div>
    </div>
  );
}
```

### 3.4 样式设计

```css
/* frontend/src/components/CountdownDisplay.css */
.countdown-display {
  padding: 16px;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  background: #f9f9f9;
}

.error-banner {
  padding: 8px;
  margin-bottom: 12px;
  background: #fff3cd;
  border: 1px solid #ffc107;
  border-radius: 4px;
  color: #856404;
  font-size: 14px;
}

.current-issue {
  font-size: 18px;
  margin-bottom: 12px;
}

.state {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 4px;
  font-weight: bold;
  margin-bottom: 12px;
}

.state-green {
  background: #4caf50;
  color: white;
}

.state-red {
  background: #f44336;
  color: white;
}

.state-yellow {
  background: #ff9800;
  color: white;
}

.state-gray {
  background: #9e9e9e;
  color: white;
}

.countdown > div {
  margin: 8px 0;
}

.countdown strong {
  color: #1976d2;
  font-size: 20px;
}
```

## 4. 实现步骤

### 4.1 后端实现
1. 创建 `backend/app/schemas/lottery.py`
2. 创建 `backend/app/api/lottery.py`
3. 在 `backend/app/engine/adapters/jnd.py` 添加 `get_current_install_detail()`
4. 修改 `backend/app/engine/worker.py` 的 `_should_bet()` 方法
5. 在 `backend/app/main.py` 注册 lottery router

### 4.2 前端实现
1. 创建 `frontend/src/types/api/lottery.ts`（含状态枚举和映射表）
2. 创建 `frontend/src/hooks/useLotteryCountdown.ts`（共享 hook）
3. 创建 `frontend/src/api/lottery.ts`
4. 创建 `frontend/src/components/CountdownDisplay.tsx`
5. 创建 `frontend/src/components/CountdownDisplay.css`
6. 在 `Dashboard.tsx` 和 `Strategies.tsx` 中使用组件

### 4.3 测试验证
1. 单元测试：测试 State 判断逻辑
2. 集成测试：测试 API 端点
3. Chrome MCP 测试：验证倒计时显示
4. 真实下注测试：验证下注成功率提升

## 5. 性能考虑

- 前端每 5 秒调用一次 API（避免频繁请求）
- 倒计时在客户端计算（减少服务器压力）
- Worker 仍然使用现有的轮询机制（不增加额外请求）

## 6. 安全考虑

- API 需要认证（使用现有的 JWT 机制）
- 限流保护（避免恶意请求）
- 任务：在 tasks.md 中明确安全项落地任务

## 7. 兼容性

- 保持现有 API 不变
- 新增 API 不影响现有功能
- Worker 改进向后兼容

## 8. 可观测性

增加以下监控指标：
- `state!=1` 跳过次数（按状态分类）
- 下注尝试次数
- 拒单原因分布（赔率变化、封盘、时间不足等）
- API 接口延迟（P50/P95/P99）
- 二次校验失败次数
