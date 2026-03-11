# 投注平台 API 接口文档

> 平台: test166 (platform_id=10)  
> 基础地址: `https://166test.com`  
> 彩种: JND28WEB (加拿大28)  
> 模板代码: JNDPCDD  
> 认证方式: Cookie-based Session (试玩模式通过 VisitorLogin 获取 token cookie)  
> 最后更新: 2026-02-28

---

## 目录

1. [认证与会话管理](#1-认证与会话管理)
2. [期号/倒计时查询 (issue_api)](#2-期号倒计时查询)
3. [赔率加载 (odds_api)](#3-赔率加载)
4. [赔率刷新 (refresh_odds_api)](#4-赔率刷新)
5. [下注提交 (bet_api)](#5-下注提交)
6. [余额/账户查询 (balance_api)](#6-余额账户查询)
7. [最新注单 (history_api)](#7-最新注单)
8. [开奖结果 (results_api)](#8-开奖结果)
9. [历史开奖批量查询 (Lotteryresult)](#9-历史开奖批量查询)
10. [长龙排行 (changlong_api)](#10-长龙排行)
11. [在线心跳 (online_api)](#11-在线心跳)
12. [公告消息 (notice_api)](#12-公告消息)
13. [Token刷新 (refresh_token_api)](#13-token刷新)
14. [玩法代码映射表](#14-玩法代码映射表)
15. [赔率参考表](#15-赔率参考表)
 
---

## 1. 认证与会话管理

### 1.1 试玩模式登录 (Visitor Login)

```
GET {base_url}/Member/VisitorLogin
```

- 直接 GET 请求，无需参数
- 服务端返回 302 重定向，跟随重定向后 Session 中会写入 `token` cookie
- 后续所有 API 请求携带此 cookie 即可认证

**请求头:**
```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: zh-CN,zh;q=0.9
Referer: {base_url}
```

**认证成功判断:** Session cookies 中包含 `token` 字段

### 1.2 用户协议页

```
GET {base_url}/Member/Agreement
```

### 1.3 标准登录 (账号密码)

通过平台登录页面提交表单:
- 用户名字段: `txtName`
- 密码字段: `txtPwd`
- 验证码字段: `txtVerify`

---

## 2. 期号/倒计时查询

**API ID:** `issue_api`  
**用途:** 获取当前期号、倒计时、上期开奖结果

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

**请求体:** 空

**成功条件:** `State == 1`

**必须字段:** `Installments`, `State`, `CloseTimeStamp`, `OpenTimeStamp`

**响应示例:**
```json
{
  "Installments": "3397187",
  "State": 1,
  "CloseTimeStamp": 51,
  "OpenTimeStamp": 71,
  "PreLotteryResult": "7,5,0",
  "PreInstallments": "3397186",
  "TemplateCode": "JNDPCDD"
}
```

**字段映射:**

| 系统字段 | 平台原始字段 | 说明 |
|---------|------------|------|
| `current_issue` | `Installments` | 当前期号 |
| `state` | `State` | 状态 (1=开盘, 其他=封盘) |
| `close_countdown` | `CloseTimeStamp` | 距封盘秒数 |
| `open_countdown` | `OpenTimeStamp` | 距开奖秒数 |
| `pre_result` | `PreLotteryResult` | 上期开奖结果 (格式: "球1,球2,球3") |
| `pre_issue` | `PreInstallments` | 上期期号 |
| `template_code` | `TemplateCode` | 彩种模板代码 |

**State 状态说明:**
- `1` = 开盘中 (可投注)
- 其他值 = 封盘/等待开奖

---

## 3. 赔率加载

**API ID:** `odds_api`  
**用途:** 获取当前期号所有玩法的赔率

```
POST {base_url}/PlaceBet/Loaddata?lotteryType=JND28WEB
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:**
```
itype=-1&midCode=HZ%2CDX%2CDS%2CZH%2CSB%2CTMBS%2CJDX%2CBZ%2CB1QH%2CB1LM%2CB2QH%2CB2LM%2CB3QH%2CB3LM%2CLHH&oddstype=A&lotteryType=JND28WEB&install={issue}
```

**参数说明:**
- `itype`: 固定 `-1`
- `midCode`: 玩法分类代码 (URL编码的逗号分隔列表)
  - `HZ` = 和值, `DX` = 大小, `DS` = 单双, `ZH` = 组合
  - `SB` = 色波, `TMBS` = 特码波色, `JDX` = 极值, `BZ` = 豹子
  - `B1QH` = 第一球号码, `B1LM` = 第一球两面
  - `B2QH` = 第二球号码, `B2LM` = 第二球两面
  - `B3QH` = 第三球号码, `B3LM` = 第三球两面
  - `LHH` = 龙虎和
- `oddstype`: 赔率类型, 固定 `A`
- `install`: 当前期号

**数据路径:** `data`

**响应示例 (data 部分):**
```json
{
  "DX1": 2.053,   "DX2": 2.053,
  "DS3": 2.053,   "DS4": 2.053,
  "JDX5": 17.655, "JDX6": 17.655,
  "ZH7": 4.293,   "ZH8": 4.725,
  "ZH9": 4.725,   "ZH10": 4.293,
  "SB1": 2.975,   "SB2": 2.975,   "SB3": 2.975,
  "BZ4": 99.17,
  "HZ1": 955,     "HZ2": 328,     "HZ3": 163,
  "HZ4": 98,      "HZ5": 65.5,    "HZ6": 47,
  "HZ7": 35.3,    "HZ8": 27.4,    "HZ9": 21.9,
  "HZ10": 17.95,  "HZ11": 15.66,  "HZ12": 14.25,
  "HZ13": 13.45,  "HZ14": 13.13,  "HZ15": 13.13,
  "HZ16": 13.45,  "HZ17": 14.25,  "HZ18": 15.66,
  "HZ19": 17.95,  "HZ20": 21.9,   "HZ21": 27.4,
  "HZ22": 35.3,   "HZ23": 47,     "HZ24": 65.5,
  "HZ25": 98,     "HZ26": 163,    "HZ27": 328,
  "HZ28": 955,
  "TMBS5": 0
}
```

---

## 4. 赔率刷新

**API ID:** `refresh_odds_api`  
**用途:** 刷新赔率数据 (轻量级, 仅返回状态)

```
POST {base_url}/PlaceBet/Refreshodds?lotteryType=JND28WEB
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:**
```
itype=-1&midCode=HZ%2CDX%2CDS%2CZH%2CSB%2CTMBS%2CJDX%2CBZ&oddstype=A&lotteryType=JND28WEB&install={issue}
```

**成功条件:** `State == 1`

**响应示例:**
```json
{
  "State": 1
}
```

---

## 5. 下注提交

**API ID:** `bet_api`  
**用途:** 提交投注请求

```
POST {base_url}/PlaceBet/Confirmbet
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体模板:**
```
{bets}&lotteryType=JND28WEB&install={issue}
```

**下注数据格式 (betdata 数组):**
```
betdata[0][Amount]=5&betdata[0][KeyCode]=DX1&betdata[0][Odds]=2.053&betdata[1][Amount]=10&betdata[1][KeyCode]=HZ13&betdata[1][Odds]=13.45
```

**字段说明:**
| 字段 | 说明 | 示例 |
|------|------|------|
| `Amount` | 下注金额 | `5` |
| `KeyCode` | 玩法代码 | `DX1` (大), `HZ13` (和值13) |
| `Odds` | 当前赔率 | `2.053` |

**完整请求体示例:**
```
betdata[0][Amount]=5&betdata[0][KeyCode]=DX1&betdata[0][Odds]=2.053&lotteryType=JND28WEB&install=3397187
```

**成功条件:** `succeed == 1`

**必须字段:** `succeed`

**响应字段映射:**

| 系统字段 | 平台原始字段 | 说明 |
|---------|------------|------|
| `success` | `succeed` | 是否成功 (1=成功) |
| `message` | `msg` | 提示消息 |
| `bet_list` | `betList` | 投注明细列表 |

**succeed 状态码:**
- `1` = 下注成功
- `5` = 赔率不一致 (赔率已变动)
- `10` = 参数不正确
- `18` = 限额为0 (该玩法暂停)
- 其他 = 其他错误

---

## 6. 余额/账户查询

**API ID:** `balance_api`  
**用途:** 查询账户余额和当日盈亏

```
POST {base_url}/PlaceBet/QueryResult?lotteryType=JND28WEB
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:** 空

**必须字段:** `accountLimit`

**响应字段映射:**

| 系统字段 | 平台原始字段 | 说明 |
|---------|------------|------|
| `balance` | `accountLimit` | 账户余额 |
| `today_pnl` | `Result` | 今日已结算盈亏 |
| `unsettled` | `UnResult` | 未结算金额 |
| `acc_type` | `AccType` | 账户类型 |

---

## 7. 最新注单

**API ID:** `history_api`  
**用途:** 查询最近投注记录

```
POST {base_url}/PlaceBet/Topbetlist
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:**
```
top=15&lotterytype=JND28WEB
```

**参数说明:**
- `top`: 返回条数 (默认15)
- `lotterytype`: 彩种类型

**响应:** 返回最近 N 条投注记录数组

---

## 8. 开奖结果

**API ID:** `results_api`  
**用途:** 查询最近开奖结果

```
POST {base_url}/ResultHistory/GetTopHistoryResults
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:**
```
lotteryType=JND28WEB&page=1&pageSize=20
```

**参数说明:**
- `lotteryType`: 彩种类型
- `page`: 页码
- `pageSize`: 每页条数

---

## 9. 历史开奖批量查询

> 此接口未在维护员界面配置, 但在执行引擎中直接调用, 用于策略历史数据预加载和补结算。

```
POST {base_url}/ResultHistory/Lotteryresult?lotterytype=JND28WEB
```

**请求头:**
```json
{
  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:**
```
start={start_row}&rows={page_size}&query=
```

**参数说明:**
- `start`: 起始行号 (从1开始)
- `rows`: 每页行数 (最大5000)
- `query`: 查询条件 (空字符串)

**响应示例:**
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

**Records 字段:**
| 字段 | 说明 |
|------|------|
| `Installments` | 期号 |
| `OpenResult` | 开奖结果 (格式: "球1,球2,球3") |
| `OpenTime` | 开奖时间 |

---

## 10. 长龙排行

**API ID:** `changlong_api`  
**用途:** 查询各玩法连续出现次数排行

```
POST {base_url}/PlaceBet/GetChangLong?lotteryType=JND28WEB
```

**请求头:**
```json
{
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:** 空

**数据路径:** `data`

**必须字段:** `data`

**响应示例 (data 部分):**
```json
{
  "tmpCode": "JNDPCDD",
  "lot": "JND28WEB",
  "data": [
    {
      "KeyCode": "LHH_L",
      "ProjectName": "龙虎和-龙",
      "MidType": "龙虎和",
      "Count": 5
    },
    {
      "KeyCode": "DS4",
      "ProjectName": "单双-双",
      "MidType": "单双",
      "Count": 3
    }
  ]
}
```

**字段映射:**

| 系统字段 | 平台原始字段 | 说明 |
|---------|------------|------|
| `template_code` | `tmpCode` | 模板代码 |
| `lottery_type` | `lot` | 彩种类型 |
| `changlong_data` | `data` | 长龙数据数组 |

---

## 11. 在线心跳

**API ID:** `online_api`  
**用途:** 保持会话活跃, 防止超时断开

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

**响应示例:**
```json
{
  "State": 1
}
```

---

## 12. 公告消息

**API ID:** `notice_api`  
**用途:** 获取平台公告消息

```
POST {base_url}/Notices/Msg
```

**请求头:**
```json
{
  "X-Requested-With": "XMLHttpRequest"
}
```

**请求体:** 空  
**超时:** 10秒  
**数据路径:** `data`

**响应字段映射:**

| 系统字段 | 平台原始字段 | 说明 |
|---------|------------|------|
| `state` | `State` | 状态 (-1=无公告) |
| `notices` | `data` | 公告数据 |

---

## 13. Token刷新

**API ID:** `refresh_token_api`  
**用途:** 刷新认证 Token, 延长会话有效期

```
POST {base_url}/Member/RefleshToken?uid={uid}&loginId={login_id}
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

**URL 参数:**
- `uid`: 用户ID
- `loginId`: 登录ID

**响应字段映射:**

| 系统字段 | 平台原始字段 | 说明 |
|---------|------------|------|
| `state` | `State` | 状态 (1=成功) |
| `token` | `token` | 新的 token 值 |

---

## 14. 玩法代码映射表

### 主要玩法

| 玩法分类 | 前缀 | 选项 | KeyCode | 典型赔率 |
|---------|------|------|---------|---------|
| **大小** | DX | 大 | DX1 | 2.053 |
| | | 小 | DX2 | 2.053 |
| **单双** | DS | 单 | DS3 | 2.053 |
| | | 双 | DS4 | 2.053 |
| **极值** | JDX | 极大 | JDX5 | 17.655 |
| | | 极小 | JDX6 | 17.655 |
| **组合** | ZH | 大单 | ZH7 | 4.293 |
| | | 大双 | ZH8 | 4.725 |
| | | 小单 | ZH9 | 4.725 |
| | | 小双 | ZH10 | 4.293 |
| **色波** | SB | 红波 | SB1 | 2.975 |
| | | 绿波 | SB2 | 2.975 |
| | | 蓝波 | SB3 | 2.975 |
| **豹子** | BZ | 豹子 | BZ4 | 99.17 |

### 和值玩法 (HZ)

| KeyCode | 和值 | 典型赔率 | KeyCode | 和值 | 典型赔率 |
|---------|------|---------|---------|------|---------|
| HZ1 | 1 | 955 | HZ15 | 15 | 13.13 |
| HZ2 | 2 | 328 | HZ16 | 16 | 13.45 |
| HZ3 | 3 | 163 | HZ17 | 17 | 14.25 |
| HZ4 | 4 | 98 | HZ18 | 18 | 15.66 |
| HZ5 | 5 | 65.5 | HZ19 | 19 | 17.95 |
| HZ6 | 6 | 47 | HZ20 | 20 | 21.9 |
| HZ7 | 7 | 35.3 | HZ21 | 21 | 27.4 |
| HZ8 | 8 | 27.4 | HZ22 | 22 | 35.3 |
| HZ9 | 9 | 21.9 | HZ23 | 23 | 47 |
| HZ10 | 10 | 17.95 | HZ24 | 24 | 65.5 |
| HZ11 | 11 | 15.66 | HZ25 | 25 | 98 |
| HZ12 | 12 | 14.25 | HZ26 | 26 | 163 |
| HZ13 | 13 | 13.45 | HZ27 | 27 | 328 |
| HZ14 | 14 | 13.13 | HZ28 | 28 | 955 |

### 单球玩法

| 玩法分类 | 前缀 | 选项 | KeyCode |
|---------|------|------|---------|
| **第一球号码** | B1QH | 0-9 | B1QH0 ~ B1QH9 |
| **第一球两面** | B1LM | 大/小/单/双 | B1LM_DA / B1LM_X / B1LM_D / B1LM_S |
| **第二球号码** | B2QH | 0-9 | B2QH0 ~ B2QH9 |
| **第二球两面** | B2LM | 大/小/单/双 | B2LM_DA / B2LM_X / B2LM_D / B2LM_S |
| **第三球号码** | B3QH | 0-9 | B3QH0 ~ B3QH9 |
| **第三球两面** | B3LM | 大/小/单/双 | B3LM_DA / B3LM_X / B3LM_D / B3LM_S |

### 龙虎和

| 选项 | KeyCode |
|------|---------|
| 龙 | LHH_L |
| 虎 | LHH_H |
| 和 | LHH_HE |

---

## 15. 赔率参考表

> 赔率为浮动赔率, 每期可能不同。以下为典型值。

### 赔率分布特征

- **大小/单双**: ~2.053 (接近50%概率)
- **组合**: ~4.293-4.725 (接近25%概率)
- **色波**: ~2.975 (接近33%概率)
- **极值**: ~17.655 (低概率高赔率)
- **豹子**: ~99.17 (极低概率)
- **和值**: 13.13-955 (中间值低赔率, 两端高赔率, 对称分布)

### 和值=13/14 特殊规则

当开奖和值为 13 或 14 时:
- **大小玩法** (DX1/DX2): 退款处理 (不计胜负, 返还本金)
- 其他玩法: 正常结算

---

## 附录: 系统内部赔率代码映射

执行引擎中存在一个 `ODDS_KEY_MAP`, 用于将策略 play_code 映射到平台赔率 API 返回的 key:

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

这是因为平台赔率 API 返回的 key 带有特定数字后缀 (如 DS3 而非 DS1), 与策略内部使用的 play_code 不一致。
