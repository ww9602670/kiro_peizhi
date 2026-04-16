"""
E2E测试上下文类

提供统一的E2E测试上下文，封装常用的测试操作。
"""
from typing import Optional
from enum import Enum
import httpx
import aiosqlite


class E2EPhase(Enum):
    """E2E测试阶段枚举"""
    INIT = "初始化"
    LOGIN = "登录"
    BIND_ACCOUNT = "绑定账号"
    ACCOUNT_LOGIN = "账号登录"
    CREATE_STRATEGY = "创建策略"
    START_STRATEGY = "启动策略"
    WAIT_BET = "等待投注"
    VERIFY_BET = "验证投注"
    WAIT_SETTLEMENT = "等待结算"
    VERIFY_SETTLEMENT = "验证结算"
    CHECK_DASHBOARD = "检查Dashboard"
    STOP_STRATEGY = "停止策略"
    CLEANUP = "清理"
    COMPLETED = "完成"


class E2ETestContext:
    """
    E2E测试上下文类
    
    管理E2E测试的生命周期，提供便捷的API调用和数据查询方法。
    """
    
    def __init__(
        self,
        db: aiosqlite.Connection,
        client: httpx.AsyncClient,
        cleanup_data: dict
    ):
        """
        初始化E2E测试上下文
        
        Args:
            db: 数据库连接
            client: HTTP客户端
            cleanup_data: 清理数据字典，用于记录需要清理的资源
        """
        self.db = db
        self.client = client
        self.cleanup_data = cleanup_data
        
        # 测试状态
        self.current_phase = E2EPhase.INIT
        
        # 测试数据
        self.token: Optional[str] = None
        self.operator_id: Optional[int] = None
        self.account_id: Optional[int] = None
        self.strategy_id: Optional[int] = None
        
    def _set_phase(self, phase: E2EPhase):
        """设置当前测试阶段"""
        self.current_phase = phase
        print(f"\n[{phase.value}] 开始执行...")
    
    def _get_headers(self) -> dict:
        """获取带认证token的请求头"""
        if not self.token:
            raise ValueError("未登录，无法获取认证头")
        return {"Authorization": f"Bearer {self.token}"}

    
    async def login(self, username: str, password: str) -> str:
        """
        执行登录操作
        
        Args:
            username: 用户名
            password: 密码
            
        Returns:
            认证token
            
        Raises:
            AssertionError: 登录失败时抛出
        """
        self._set_phase(E2EPhase.LOGIN)
        
        resp = await self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password}
        )
        
        # 验证响应状态码
        assert resp.status_code == 200, f"登录失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        # 解析响应
        body = resp.json()
        assert body["code"] == 0, f"登录失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        # 保存token
        self.token = body["data"]["token"]
        
        # 保存token到cleanup_data（用于清理）
        self.cleanup_data["token"] = self.token
        
        # 解析token获取operator_id
        import jwt
        from app.utils.auth import SECRET_KEY
        payload = jwt.decode(
            self.token, 
            SECRET_KEY, 
            algorithms=["HS256"],
            options={"verify_iat": False}  # 跳过iat验证，避免时钟偏移问题
        )
        self.operator_id = int(payload["sub"])
        
        # 记录到清理数据（如果是测试创建的用户）
        if username.startswith("e2e_test_"):
            self.cleanup_data.setdefault("operator_ids", []).append(self.operator_id)
        
        print(f"✓ 登录成功: operator_id={self.operator_id}")
        return self.token

    
    async def bind_account(
        self,
        account_name: str,
        password: str,
        platform_type: str = "JND28WEB"
    ) -> int:
        """
        绑定测试账号（如果已存在则返回现有账号ID）
        
        Args:
            account_name: 账号名
            password: 密码
            platform_type: 平台类型
            
        Returns:
            账号ID
        """
        self._set_phase(E2EPhase.BIND_ACCOUNT)
        
        # 先检查是否已存在相同账号
        resp_list = await self.client.get(
            "/api/v1/accounts",
            headers=self._get_headers(),
        )
        
        if resp_list.status_code == 200:
            body = resp_list.json()
            if body["code"] == 0:
                for account in body["data"]:
                    if account["account_name"] == account_name and account["platform_type"] == platform_type:
                        self.account_id = account["id"]
                        print(f"✓ 使用现有账号: account_id={self.account_id}")
                        return self.account_id
        
        # 账号不存在，创建新账号
        resp = await self.client.post(
            "/api/v1/accounts",
            headers=self._get_headers(),
            json={
                "account_name": account_name,
                "password": password,
                "platform_type": platform_type,
            }
        )
        
        assert resp.status_code == 200, f"绑定账号失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        body = resp.json()
        assert body["code"] == 0, f"绑定账号失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        self.account_id = body["data"]["id"]
        self.cleanup_data.setdefault("account_ids", []).append(self.account_id)
        
        print(f"✓ 绑定账号成功: account_id={self.account_id}")
        return self.account_id
    
    async def account_login(self, account_id: int) -> dict:
        """
        账号登录（获取余额和赔率）
        
        Args:
            account_id: 账号ID
            
        Returns:
            账号信息字典
        """
        self._set_phase(E2EPhase.ACCOUNT_LOGIN)
        
        resp = await self.client.post(
            f"/api/v1/accounts/{account_id}/login",
            headers=self._get_headers(),
        )
        
        assert resp.status_code == 200, f"账号登录失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        body = resp.json()
        assert body["code"] == 0, f"账号登录失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        account_data = body["data"]
        assert account_data["status"] == "online", f"账号状态不是online: {account_data['status']}"
        
        print(f"✓ 账号登录成功: status={account_data['status']}, balance={account_data.get('balance', 'N/A')}")
        return account_data
    
    async def get_account(self, account_id: int) -> dict:
        """
        查询账号详情（通过API）
        
        Args:
            account_id: 账号ID
            
        Returns:
            账号信息字典
        """
        resp = await self.client.get(
            "/api/v1/accounts",
            headers=self._get_headers(),
        )
        assert resp.status_code == 200, f"查询账号失败，状态码: {resp.status_code}"
        
        body = resp.json()
        assert body["code"] == 0, f"查询账号失败，错误码: {body['code']}"
        
        # 从账号列表中找到指定ID的账号
        accounts = body["data"]
        for account in accounts:
            if account["id"] == account_id:
                return account
        
        raise ValueError(f"未找到账号: account_id={account_id}")

    
    async def create_strategy(self, config: dict) -> int:
        """
        创建投注策略
        
        Args:
            config: 策略配置字典，包含:
                - account_id: 账号ID
                - name: 策略名称
                - type: 策略类型 (flat/martin)
                - play_code: 玩法代码
                - base_amount: 基础金额
                - simulation: 是否模拟 (可选，默认1)
                
        Returns:
            策略ID
        """
        self._set_phase(E2EPhase.CREATE_STRATEGY)
        
        resp = await self.client.post(
            "/api/v1/strategies",
            headers=self._get_headers(),
            json=config
        )
        
        assert resp.status_code == 200, f"创建策略失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        body = resp.json()
        assert body["code"] == 0, f"创建策略失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        self.strategy_id = body["data"]["id"]
        self.cleanup_data.setdefault("strategy_ids", []).append(self.strategy_id)
        
        print(f"✓ 创建策略成功: strategy_id={self.strategy_id}, name={config.get('name', 'N/A')}")
        return self.strategy_id
    
    async def start_strategy(self, strategy_id: int) -> dict:
        """
        启动策略
        
        Args:
            strategy_id: 策略ID
            
        Returns:
            策略信息字典
        """
        self._set_phase(E2EPhase.START_STRATEGY)
        
        resp = await self.client.post(
            f"/api/v1/strategies/{strategy_id}/start",
            headers=self._get_headers(),
        )
        
        assert resp.status_code == 200, f"启动策略失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        body = resp.json()
        assert body["code"] == 0, f"启动策略失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        strategy_data = body["data"]
        assert strategy_data["status"] == "running", f"策略状态不是running: {strategy_data['status']}"
        
        print(f"✓ 启动策略成功: strategy_id={strategy_id}, status={strategy_data['status']}")
        return strategy_data
    
    async def stop_strategy(self, strategy_id: int) -> dict:
        """
        停止策略
        
        Args:
            strategy_id: 策略ID
            
        Returns:
            策略信息字典
        """
        self._set_phase(E2EPhase.STOP_STRATEGY)
        
        resp = await self.client.post(
            f"/api/v1/strategies/{strategy_id}/stop",
            headers=self._get_headers(),
        )
        
        assert resp.status_code == 200, f"停止策略失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        body = resp.json()
        assert body["code"] == 0, f"停止策略失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        strategy_data = body["data"]
        print(f"✓ 停止策略成功: strategy_id={strategy_id}, status={strategy_data['status']}")
        return strategy_data
    
    async def get_strategy(self, strategy_id: int) -> dict:
        """
        查询策略详情（通过API）
        
        Args:
            strategy_id: 策略ID
            
        Returns:
            策略信息字典
        """
        resp = await self.client.get(
            "/api/v1/strategies",
            headers=self._get_headers(),
        )
        assert resp.status_code == 200, f"查询策略失败，状态码: {resp.status_code}"
        
        body = resp.json()
        assert body["code"] == 0, f"查询策略失败，错误码: {body['code']}"
        
        # 从策略列表中找到指定ID的策略
        strategies = body["data"]
        for strategy in strategies:
            if strategy["id"] == strategy_id:
                return strategy
        
        raise ValueError(f"未找到策略: strategy_id={strategy_id}")

    
    async def wait_for_bet(self, strategy_id: int, timeout: int = 30) -> dict:
        """
        等待投注完成（通过API查询）
        
        Args:
            strategy_id: 策略ID
            timeout: 超时时间（秒）
            
        Returns:
            投注订单信息字典（状态为bet_success或bet_failed）
        """
        self._set_phase(E2EPhase.WAIT_BET)
        
        import asyncio
        import time
        
        start_time = time.time()
        print(f"等待投注... (超时: {timeout}秒)")
        
        while time.time() - start_time < timeout:
            try:
                # 通过API查询投注订单
                resp = await self.client.get(
                    f"/api/v1/bet-orders?strategy_id={strategy_id}",
                    headers=self._get_headers(),
                )
                
                if resp.status_code == 200:
                    body = resp.json()
                    if body["code"] == 0 and body["data"]:
                        # API返回的数据结构是 {"data": {"items": [...], "total": ...}}
                        data = body["data"]
                        orders = data.get("items", []) if isinstance(data, dict) else data
                        if orders and len(orders) > 0:
                            order = orders[0]  # 获取最新的订单
                            # 只有当订单状态为bet_success或bet_failed时才返回
                            if order.get("status") in ["bet_success", "bet_failed"]:
                                print(f"✓ 检测到投注: order_id={order.get('id', 'N/A')}, status={order.get('status', 'N/A')}, amount={order.get('amount', 'N/A')}")
                                return order
                elif resp.status_code != 200:
                    print(f"API返回错误状态码: {resp.status_code}, 响应: {resp.text[:200]}")
            except KeyError as e:
                print(f"KeyError: {e}, 响应体: {body if 'body' in locals() else 'N/A'}")
            except Exception as e:
                print(f"查询投注订单时出错: {type(e).__name__}: {e}")
            
            await asyncio.sleep(0.5)
        
        raise TimeoutError(f"等待投注超时 ({timeout}秒)")
    
    async def get_order(self, order_id: int) -> dict:
        """
        查询订单详情（通过API）
        
        Args:
            order_id: 订单ID
            
        Returns:
            订单信息字典
        """
        resp = await self.client.get(
            f"/api/v1/bet-orders/{order_id}",
            headers=self._get_headers(),
        )
        assert resp.status_code == 200, f"查询订单失败，状态码: {resp.status_code}"
        
        body = resp.json()
        assert body["code"] == 0, f"查询订单失败，错误码: {body['code']}"
        
        return body["data"]
    
    async def get_bet_orders(self, filters: dict = None) -> list[dict]:
        """
        查询投注订单列表（通过API）
        
        Args:
            filters: 过滤条件字典，如 {"strategy_id": 1, "status": "bet_success"}
            
        Returns:
            订单列表
        """
        # 构建查询参数
        params = []
        if filters:
            for key, value in filters.items():
                params.append(f"{key}={value}")
        
        query_string = "&".join(params) if params else ""
        url = f"/api/v1/bet-orders?{query_string}" if query_string else "/api/v1/bet-orders"
        
        resp = await self.client.get(
            url,
            headers=self._get_headers(),
        )
        
        if resp.status_code == 200:
            body = resp.json()
            if body["code"] == 0:
                return body["data"]
        
        return []

    
    async def wait_for_lottery_result(self, issue: str, timeout: int = 300) -> dict:
        """
        等待指定期号的开奖结果
        
        Args:
            issue: 期号
            timeout: 超时时间（秒），默认300秒（5分钟）
            
        Returns:
            开奖结果字典
        """
        import asyncio
        import time
        
        start_time = time.time()
        print(f"等待期号 {issue} 开奖... (超时: {timeout}秒)")
        
        while time.time() - start_time < timeout:
            result = await self.get_lottery_result(issue)
            
            if result is not None:
                print(f"✓ 期号 {issue} 已开奖: {result.get('open_result', 'N/A')}")
                return result
            
            await asyncio.sleep(2.0)  # 每2秒查询一次
        
        raise TimeoutError(f"等待期号 {issue} 开奖超时 ({timeout}秒)")
    
    async def wait_for_settlement(self, order_id: int, timeout: int = 60) -> dict:
        """
        等待订单结算完成（开奖驱动）
        
        Args:
            order_id: 订单ID
            timeout: 超时时间（秒），默认60秒
            
        Returns:
            已结算的订单信息字典
        """
        self._set_phase(E2EPhase.WAIT_SETTLEMENT)
        
        import asyncio
        import time
        
        # 1. 获取订单信息
        order = await self.get_order(order_id)
        issue = order["issue"]
        
        print(f"等待订单 {order_id} 结算（期号: {issue}）...")
        
        # 2. 先等待开奖（最多5分钟）
        try:
            await self.wait_for_lottery_result(issue, timeout=300)
        except TimeoutError:
            print(f"⚠️ 期号 {issue} 开奖超时，继续等待结算...")
        
        # 3. 开奖后等待结算完成（最多60秒）
        start_time = time.time()
        print(f"等待结算处理... (超时: {timeout}秒)")
        
        while time.time() - start_time < timeout:
            order = await self.get_order(order_id)
            
            if order["status"] == "settled":
                print(f"✓ 订单已结算: order_id={order_id}, is_win={order['is_win']}, pnl={order['pnl']}")
                return order
            
            await asyncio.sleep(1.0)
        
        raise TimeoutError(f"等待结算超时 ({timeout}秒)")
    
    async def get_lottery_result(self, issue: str) -> dict:
        """
        查询开奖结果（通过API）
        
        Args:
            issue: 期号
            
        Returns:
            开奖结果字典，如果不存在返回None
        """
        resp = await self.client.get(
            f"/api/v1/lottery/results/{issue}",
            headers=self._get_headers(),
        )
        
        if resp.status_code == 200:
            body = resp.json()
            if body["code"] == 0:
                return body["data"]
        
        return None
    
    async def verify_settlement_data(self, order_id: int) -> bool:
        """
        验证结算数据的正确性
        
        检查：
        1. 订单状态为settled
        2. 策略盈亏已更新
        3. 账号余额已更新
        
        Args:
            order_id: 订单ID
            
        Returns:
            验证是否通过
        """
        order = await self.get_order(order_id)
        
        # 1. 检查订单状态
        if order["status"] != "settled":
            print(f"✗ 订单状态不是settled: {order['status']}")
            return False
        
        # 2. 检查策略盈亏
        strategy = await self.get_strategy(order["strategy_id"])
        print(f"  策略盈亏: daily_pnl={strategy['daily_pnl']}, total_pnl={strategy['total_pnl']}")
        
        # 3. 检查账号余额
        account = await self.get_account(order["account_id"])
        print(f"  账号余额: balance={account['balance']}")
        
        print(f"✓ 结算数据验证通过")
        return True

    
    async def get_dashboard(self) -> dict:
        """
        获取Dashboard数据
        
        Returns:
            Dashboard数据字典
        """
        self._set_phase(E2EPhase.CHECK_DASHBOARD)
        
        resp = await self.client.get(
            "/api/v1/dashboard",
            headers=self._get_headers(),
        )
        
        assert resp.status_code == 200, f"获取Dashboard失败，状态码: {resp.status_code}, 响应: {resp.text}"
        
        body = resp.json()
        assert body["code"] == 0, f"获取Dashboard失败，错误码: {body['code']}, 消息: {body.get('message', '')}"
        
        dashboard_data = body["data"]
        print(f"✓ Dashboard数据: balance={dashboard_data.get('balance', 'N/A')}, "
              f"daily_pnl={dashboard_data.get('daily_pnl', 'N/A')}, "
              f"total_pnl={dashboard_data.get('total_pnl', 'N/A')}")
        
        return dashboard_data
    
    async def verify_dashboard_data(self, expected: dict = None) -> bool:
        """
        验证Dashboard数据的正确性
        
        Args:
            expected: 期望的数据字典（可选）
            
        Returns:
            验证是否通过
        """
        dashboard = await self.get_dashboard()
        
        # 验证必需字段存在
        required_fields = ["balance", "daily_pnl", "total_pnl", "running_strategies", "recent_bets"]
        for field in required_fields:
            if field not in dashboard:
                print(f"✗ Dashboard缺少字段: {field}")
                return False
        
        # 如果提供了期望值，进行比较
        if expected:
            for key, expected_value in expected.items():
                actual_value = dashboard.get(key)
                if actual_value != expected_value:
                    print(f"✗ Dashboard字段不匹配: {key}, 期望={expected_value}, 实际={actual_value}")
                    return False
        
        print(f"✓ Dashboard数据验证通过")
        return True
