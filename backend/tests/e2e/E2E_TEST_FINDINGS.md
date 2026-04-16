# E2E测试执行结果报告

## 测试日期
2026-03-13

## 测试环境
- 后端服务: http://localhost:8888
- 测试框架: pytest + httpx
- 数据库: SQLite (in-memory)

## 测试执行情况

### 成功的测试步骤
1. ✅ JWT Token解码问题已修复 (添加 `verify_iat=False` 选项)
2. ✅ 平台类型配置已修正 (JND28WEB → MOCK)
3. ✅ 用户登录成功 (admin/admin123)
4. ✅ 账号绑定成功 (account_id=21, test166)
5. ✅ 账号登录成功 (status=online, balance=20000.0)
6. ✅ 策略创建成功 (strategy_id=26, name=E2E测试策略)
7. ✅ 策略启动成功 (status=running)

### 失败的测试步骤
8. ❌ 等待投注超时 (60秒内未检测到投注订单)

## 根本原因分析

### 问题1: 数据库连接不一致
**现象**: E2E测试连接到 `data/bocai.db` 文件，但后端服务使用in-memory数据库

**原因**: 
- `tests/conftest.py` 设置了 `os.environ["BOCAI_DB_PATH"] = ":memory:"`
- 后端服务启动时继承了这个环境变量
- E2E测试的 `e2e_db` fixture 连接到 `data/bocai.db`，但后端实际使用 `:memory:`

**验证**:
```python
# 通过API查询 (后端的in-memory数据库)
Total strategies: 1
Latest strategy: id=26, name=E2E测试策略, status=running

# 通过文件查询 (data/bocai.db)
Strategy 26: Not found
```

### 问题2: 投注未执行
**现象**: 策略状态为running，但60秒内未产生任何投注订单

**可能原因**:
1. 策略worker未正常启动
2. 平台处于停盘时间 (downtime_ranges)
3. 模拟模式 (simulation=1) 配置问题
4. 期号信息获取失败
5. 赔率数据未加载

## 解决方案

### 方案1: 修复数据库连接 (推荐)
E2E测试应该完全通过API进行，不直接访问数据库：

```python
# 不要这样做 (直接查询数据库)
cursor = await self.db.execute("SELECT * FROM bet_orders WHERE strategy_id=?", (strategy_id,))

# 应该这样做 (通过API查询)
resp = await self.client.get(f"/api/v1/bet-orders?strategy_id={strategy_id}", headers=self._get_headers())
```

**优点**:
- 真正的E2E测试 (只测试API接口)
- 不依赖数据库实现细节
- 更接近真实用户使用场景

**缺点**:
- 需要后端提供完整的查询API
- 某些内部状态可能无法直接查询

### 方案2: 确保后端使用文件数据库
修改后端启动方式，确保使用 `data/bocai.db`：

```bash
# 启动前清除环境变量
unset BOCAI_DB_PATH
# 或显式指定
export BOCAI_DB_PATH=data/bocai.db
uvicorn app.main:app --host 0.0.0.0 --port 8888
```

**优点**:
- E2E测试可以直接查询数据库
- 便于调试和验证内部状态

**缺点**:
- 需要重启后端服务
- 测试数据会污染数据库文件

### 方案3: 混合方案
- 主要通过API进行测试
- 仅在必要时 (如验证内部一致性) 查询数据库
- 确保E2E测试连接到正确的数据库 (通过环境变量或配置)

## 投注未执行问题的调查建议

1. **检查worker状态**
   ```python
   # 通过API查询worker状态
   GET /api/v1/strategies/{strategy_id}
   # 检查 worker_status 字段
   ```

2. **检查平台时间**
   ```python
   # 检查当前是否在停盘时间
   downtime_ranges = [("19:56", "20:33"), ("06:00", "07:00")]
   ```

3. **检查期号信息**
   ```python
   # 通过API查询当前期号
   GET /api/v1/lottery/current
   ```

4. **检查日志**
   ```bash
   # 查看后端日志，搜索strategy_id=26相关的日志
   # 查找worker启动、投注决策、错误信息等
   ```

5. **手动触发投注**
   ```python
   # 如果有手动投注API，可以测试投注功能是否正常
   POST /api/v1/strategies/{strategy_id}/manual-bet
   ```

## 下一步行动

### 短期 (立即执行)
1. 检查后端日志，确认strategy 26的worker是否正常运行
2. 检查当前时间是否在停盘时间内
3. 验证模拟模式是否正确配置

### 中期 (本周完成)
1. 重构E2E测试，改为完全基于API的测试方式
2. 添加更详细的日志输出，便于调试
3. 添加超时前的状态检查 (worker状态、期号信息、赔率数据等)

### 长期 (下个迭代)
1. 添加E2E测试的前置条件检查 (平台状态、时间窗口等)
2. 实现测试数据隔离机制
3. 添加更多的错误场景测试

## 测试结论

### 当前状态
- 基础功能 (登录、绑定、创建策略) 工作正常 ✅
- 核心功能 (自动投注) 未能验证 ❌
- 测试框架存在架构问题 (数据库连接不一致) ⚠️

### 建议
1. **不要**将此测试结果作为系统可用性的判断依据
2. **优先**修复数据库连接问题
3. **调查**投注未执行的根本原因
4. **重构**E2E测试为纯API测试

## 附录: 测试配置

```python
# E2E测试配置
TEST_USERNAME = "admin"
TEST_PASSWORD = "admin123"
TEST_ACCOUNT_NAME = "test166"
TEST_ACCOUNT_PASSWORD = "test166"
TEST_PLATFORM_TYPE = "MOCK"
TEST_STRATEGY_TYPE = "flat"
TEST_PLAY_CODE = "DX1"
TEST_BASE_AMOUNT = 10.0
API_BASE_URL = "http://localhost:8888"
```

## 附录: 错误日志

```
TimeoutError: 等待投注超时 (60秒)
  at tests\e2e\helpers\context.py:354

sqlite3.OperationalError: no such table: accounts
  at cleanup fixture (e2e_db trying to delete from wrong database)
```
