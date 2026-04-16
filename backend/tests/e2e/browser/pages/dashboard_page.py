"""
Dashboard页面对象
"""
from playwright.async_api import Page


class DashboardPage:
    """Dashboard页面"""
    
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url
        
        # 页面元素选择器
        self.balance_display = '[data-testid="balance"], .balance-value'
        self.daily_pnl_display = '[data-testid="daily-pnl"], .daily-pnl-value'
        self.total_pnl_display = '[data-testid="total-pnl"], .total-pnl-value'
        self.running_strategies_count = '[data-testid="running-strategies"], .running-strategies-count'
        self.recent_bets_table = '[data-testid="recent-bets"], .recent-bets-table'
        
        # 导航菜单
        self.accounts_menu = 'a[href="/accounts"], text=账号管理'
        self.strategies_menu = 'a[href="/strategies"], text=策略管理'
        self.bet_orders_menu = 'a[href="/bet-orders"], text=投注订单'
        self.alerts_menu = 'a[href="/alerts"], text=告警'
    
    async def goto(self):
        """导航到Dashboard页面"""
        await self.page.goto(f"{self.base_url}/dashboard")
        await self.page.wait_for_load_state("networkidle")
    
    async def get_balance(self):
        """获取余额"""
        try:
            text = await self.page.text_content(self.balance_display, timeout=5000)
            return float(text.replace(',', '').replace('¥', '').strip())
        except:
            return None
    
    async def get_daily_pnl(self):
        """获取当日盈亏"""
        try:
            text = await self.page.text_content(self.daily_pnl_display, timeout=5000)
            return float(text.replace(',', '').replace('¥', '').strip())
        except:
            return None
    
    async def navigate_to_accounts(self):
        """导航到账号管理页面"""
        await self.page.click(self.accounts_menu)
        await self.page.wait_for_url("**/accounts")
    
    async def navigate_to_strategies(self):
        """导航到策略管理页面"""
        await self.page.click(self.strategies_menu)
        await self.page.wait_for_url("**/strategies")
    
    async def navigate_to_bet_orders(self):
        """导航到投注订单页面"""
        await self.page.click(self.bet_orders_menu)
        await self.page.wait_for_url("**/bet-orders")
    
    async def wait_for_data_update(self, timeout: int = 5000):
        """等待数据更新（通过网络请求判断）"""
        await self.page.wait_for_timeout(timeout)
