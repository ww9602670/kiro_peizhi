# 第一期投注平台 API 接口文档

> 基于试玩平台 API 文档校验整理，标注试玩→实际投注的差异点  
> 目标平台: `https://3894703925-vvc0.mm555.co`  
> 彩种: JND28WEB (加拿大28)  
> 模板代码: JNDPCDD  
> 最后更新: 2026-02-28

---

## 试玩 vs 实际投注 差异总结

| 项目 | 试玩模式 | 实际投注模式 | 差异说明 |
|------|---------|-------------|---------|
| 基础地址 | `https://166test.com` | `https://3894703925-vvc0.mm555.co` | 域名不同，需配置化 |
| 登录方式 | `GET /Member/VisitorLogin`（无参数） | `POST` 表单登录（账号+密码+验证码） | **核心差异**，需实现验证码识别 |
| 认证方式 | Cookie-based Session（token cookie） | 预计相同（Cookie-based Session） | 需实际验证 |
| 彩种/玩法 | JND28WEB / JNDPCDD | 预计相同 | 需实际验证 |
| API 路径 | 同下方各接口 | 预计相同（路径一致，域名不同） | 需实际验证 |
| 赔率 | 试玩赔率 | 实际赔率（可能不同） | 赔率为浮动值，每期不同 |
| 余额 | 试玩余额 | 真实余额 | 金额为真实资金 |

### ⚠️ 需实际抓包验证的项目

以下项目在试玩模式下已验证通过，但实际投注模式可能存在差异，**开发第一步必须抓包确认**：

1. **登录流程**：表单字段名（`txtName`/`txtPwd`/`txtVerify`）是否一致
2. **验证码接口**：验证码图片的获取 URL（试玩文档未涉及）
3. **Session 机制**：token cookie 的名称和有效期
4. **下注限额**：实际账号可能有单注限额、单期限额
5. **结算接口**：试玩文档中的 `history_api` 返回格式是否包含结算盈亏字段
6. **Token 刷新**：`uid` 和 `loginId` 参数的获取方式

---

## 1. 认证与会话管理

### 1.1 标准登录（实际投注模式）

```
POST {base_url}/Member/Login  (待抓包确认具体路径)
```

**表单字段（基于试玩文档推测，需抓包确认）:**
| 字段 | 说明 |
|------|------|
| `txtName` | 账号 |
| `txtPwd` | 密码 |
| `txtVerify` | 验证码 |

**验证码获取（待抓包确认）:**
```
GET {base_url}/Member/GetVerifyCode  (推测路径，需确认)
```
- 返回图片验证码
- 需接入 OCR 或验证码识别服务

**认证成功判断:** Session cookies 中包含 `token` 字段

### 1.2 会话心跳

```
POST {base_url}/Member/Online
```

**请求头:**
```json
{
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:** 空  
**超时:** 10秒  
**成功条件:** `State == 1`

**用途:** 保持会话活跃，防止超时断开。建议每 60-90 秒调用一次。

### 1.3 Token 刷新

```
POST {base_url}/Member/RefleshToken?uid={uid}&loginId={login_id}
```

**成功条件:** `State == 1`  
**返回:** 新的 token 值

---

## 2. 期号/倒计时查询

```
POST {base_url}/PlaceBet/GetCurrentInstall?lotteryType=JND28WEB
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded",
  "X-Requested-With": "XMLHttpRequest"
}
```

**响应字段:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `Installments` | string | 当前期号 |
| `State` | int | 1=开盘可投注，其他=封盘 |
| `CloseTimeStamp` | int | 距封盘秒数 |
| `OpenTimeStamp` | int | 距开奖秒数 |
| `PreLotteryResult` | string | 上期开奖结果，格式 "球1,球2,球3" |
| `PreInstallments` | string | 上期期号 |
| `TemplateCode` | string | 彩种模板代码 JNDPCDD |

**投注引擎核心依赖:**
- `State == 1` 时才可下注（封盘检测）
- `CloseTimeStamp` 用于计算下注时机
- `PreLotteryResult` 用于结算上期

---

## 3. 赔率查询

```
POST {base_url}/PlaceBet/Loaddata?lotteryType=JND28WEB
```

**请求体:**
```
itype=-1&midCode=HZ%2CDX%2CDS%2CZH%2CSB%2CTMBS%2CJDX%2CBZ%2CB1QH%2CB1LM%2CB2QH%2CB2LM%2CB3QH%2CB3LM%2CLHH&oddstype=A&lotteryType=JND28WEB&install={期号}
```

**响应:** `data` 对象，key 为玩法代码，value 为赔率浮点数

**下注前必须获取最新赔率**，因为赔率为浮动值，下注时需提交当前赔率。

---

## 4. 下注提交

```
POST {base_url}/PlaceBet/Confirmbet
```

**请求体格式:**
```
betdata[0][Amount]={金额}&betdata[0][KeyCode]={玩法代码}&betdata[0][Odds]={赔率}&lotteryType=JND28WEB&install={期号}
```

**支持单次提交多注:**
```
betdata[0][Amount]=5&betdata[0][KeyCode]=DX1&betdata[0][Odds]=2.053&betdata[1][Amount]=10&betdata[1][KeyCode]=HZ13&betdata[1][Odds]=13.45&lotteryType=JND28WEB&install=3397187
```

**响应状态码:**

| succeed 值 | 含义 | 处理方式 |
|-----------|------|---------|
| 1 | 下注成功 | 记录注单 |
| 5 | 赔率不一致（已变动） | 重新获取赔率后重试 |
| 10 | 参数不正确 | 记录错误，不重试 |
| 18 | 限额为0（玩法暂停） | 记录错误，不重试 |
| 其他 | 其他错误 | 记录错误 |

---

## 5. 余额/账户查询

```
POST {base_url}/PlaceBet/QueryResult?lotteryType=JND28WEB
```

**响应字段:**

| 字段 | 说明 |
|------|------|
| `accountLimit` | 账户余额 |
| `Result` | 今日已结算盈亏 |
| `UnResult` | 未结算金额 |
| `AccType` | 账户类型 |

---

## 6. 投注记录查询

```
POST {base_url}/PlaceBet/Topbetlist
```

**请求体:**
```
top=15&lotterytype=JND28WEB
```

**用途:** 获取最近 N 条投注记录，用于结算校验和记录展示。

---

## 7. 开奖结果查询

### 7.1 最近开奖

```
POST {base_url}/ResultHistory/GetTopHistoryResults
```

**请求体:**
```
lotteryType=JND28WEB&page=1&pageSize=20
```

### 7.2 历史开奖批量查询

```
POST {base_url}/ResultHistory/Lotteryresult?lotterytype=JND28WEB
```

**请求体:**
```
start={起始行号}&rows={每页行数}&query=
```

**响应:**
```json
{
  "data": {
    "Records": [
      {
        "Installments": "3397186",
        "OpenResult": "7,5,0",
        "OpenTime": "2026-02-28 12:00:00"
      }
    ],
    "TotalCount": 50000
  }
}
```

---

## 8. 辅助接口

### 8.1 长龙排行

```
POST {base_url}/PlaceBet/GetChangLong?lotteryType=JND28WEB
```

**用途:** 各玩法连续出现次数排行，可用于策略参考。

### 8.2 公告消息

```
POST {base_url}/Notices/Msg
```

**用途:** 获取平台公告，可用于检测停盘通知。

---

## 9. 玩法代码映射表（完整版）

### 第一期支持的玩法

| 玩法分类 | KeyCode | 说明 | 典型赔率 |
|---------|---------|------|---------|
| **大小** | DX1 | 大（≥14） | 2.053 |
| | DX2 | 小（≤13） | 2.053 |
| **单双** | DS3 | 单 | 2.053 |
| | DS4 | 双 | 2.053 |
| **极值** | JDX5 | 极大（22-27） | 17.655 |
| | JDX6 | 极小（0-5） | 17.655 |
| **组合** | ZH7 | 大单 | 4.293 |
| | ZH8 | 大双 | 4.725 |
| | ZH9 | 小单 | 4.725 |
| | ZH10 | 小双 | 4.293 |
| **特码(和值)** | HZ1~HZ28 | 和值 0~27 | 13.13~955 |
| **色波** | SB1 | 红波 | 2.975 |
| | SB2 | 绿波 | 2.975 |
| | SB3 | 蓝波 | 2.975 |
| **豹子** | BZ4 | 豹子 | 99.17 |
| **第一球号码** | B1QH0~B1QH9 | 第一球 0-9 | - |
| **第一球两面** | B1LM_DA / B1LM_X / B1LM_D / B1LM_S | 大/小/单/双 | - |
| **第二球号码** | B2QH0~B2QH9 | 第二球 0-9 | - |
| **第二球两面** | B2LM_DA / B2LM_X / B2LM_D / B2LM_S | 大/小/单/双 | - |
| **第三球号码** | B3QH0~B3QH9 | 第三球 0-9 | - |
| **第三球两面** | B3LM_DA / B3LM_X / B3LM_D / B3LM_S | 大/小/单/双 | - |
| **龙虎和** | LHH_L / LHH_H / LHH_HE | 龙/虎/和 | - |

### 内部代码 → 平台代码映射

策略引擎内部使用的 play_code 与平台 API 的 KeyCode 存在映射关系：

```python
ODDS_KEY_MAP = {
    "DS1": "DS3",   # 单双-单
    "DS2": "DS4",   # 单双-双
    "JZ1": "JDX5",  # 极值-极大
    "JZ2": "JDX6",  # 极值-极小
    "ZH1": "ZH7",   # 组合-大单
    "ZH2": "ZH8",   # 组合-大双
    "ZH3": "ZH9",   # 组合-小单
    "ZH4": "ZH10",  # 组合-小双
    "BZ1": "BZ4",   # 豹子
}
```

---

## 10. 特殊结算规则

### 盘口类型

平台存在两种盘口，lotteryType 不同，结算规则不同：

| 盘口 | lotteryType | 说明 |
|------|------------|------|
| JND网盘 | `JND28WEB` | 和值 13/14 正常结算 |
| JND2.0 | `JND282` | 和值 13/14 部分玩法退款 |

**所有 API 请求中的 `lotteryType` 参数需根据操作者选择的盘口类型动态设置。**

### JND网盘（JND28WEB）结算规则
- 和值 13/14 时，所有玩法（包括大小、单双、组合）均按显示赔率正常结算
- 无退款规则

### JND2.0（JND282）结算规则

**和值 = 14 时：**
| 玩法 | 投注项 | 结算方式 |
|------|--------|---------|
| 大小 | 大 | 退款（和局） |
| 大小 | 小 | 正常结算（中奖） |
| 单双 | 双 | 退款（和局） |
| 单双 | 单 | 正常结算（未中奖） |
| 组合 | 大双 | 退款（和局） |
| 组合 | 其他 | 正常结算 |

**和值 = 13 时：**
| 玩法 | 投注项 | 结算方式 |
|------|--------|---------|
| 大小 | 小 | 退款（和局） |
| 大小 | 大 | 正常结算（未中奖） |
| 单双 | 单 | 退款（和局） |
| 单双 | 双 | 正常结算（未中奖） |
| 组合 | 小单 | 退款（和局） |
| 组合 | 其他 | 正常结算 |

**退款对马丁策略的影响：** 退款期不算命中也不算未命中，马丁序列位置不变。

---

## 11. 通用请求规范

### 请求头

所有 API 请求必须包含：
```json
{
  "X-Requested-With": "XMLHttpRequest",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
```

POST 请求额外包含：
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
}
```

### Cookie 认证

所有 API 请求必须携带登录后获取的 Session Cookie（包含 `token` 字段）。

### 错误处理

- 网络超时：重试最多 3 次，间隔递增
- 401/403：Session 过期，触发重新登录
- 5xx：服务端错误，记录日志，暂停投注
