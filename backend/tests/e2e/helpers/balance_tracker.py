"""
余额追踪器

自动追踪和验证账号余额变化，用于E2E测试中的余额一致性验证。
"""
from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import E2ETestContext


class BalanceTracker:
    """
    余额追踪器
    
    自动记录余额快照并验证余额变化的正确性。
    
    使用示例:
        tracker = BalanceTracker(ctx, account_id)
        
        await tracker.snapshot("投注前")
        order = await ctx.wait_for_bet(strategy_id)
        await tracker.snapshot("投注后")
        tracker.verify_deduction(order["amount"])
        
        settled_order = await ctx.wait_for_settlement(order["id"])
        await tracker.snapshot("结算后")
        tracker.verify_settlement(settled_order["pnl"])
    """
    
    def __init__(self, ctx: "E2ETestContext", account_id: int):
        """
        初始化余额追踪器
        
        Args:
            ctx: E2E测试上下文
            account_id: 账号ID
        """
        self.ctx = ctx
        self.account_id = account_id
        self.snapshots: List[Tuple[str, float]] = []
    
    async def snapshot(self, label: str) -> float:
        """
        记录当前余额快照
        
        Args:
            label: 快照标签（如"投注前"、"投注后"）
            
        Returns:
            当前余额
        """
        account = await self.ctx.get_account(self.account_id)
        balance = account["balance"]
        self.snapshots.append((label, balance))
        print(f"[余额快照] {label}: {balance}")
        return balance
    
    def verify_deduction(self, bet_amount: float, tolerance: float = 0.01) -> None:
        """
        验证余额扣减是否正确
        
        Args:
            bet_amount: 投注金额
            tolerance: 允许的误差范围（默认0.01）
            
        Raises:
            AssertionError: 余额扣减不正确时抛出
        """
        if len(self.snapshots) < 2:
            raise ValueError("需要至少2个快照才能验证扣减")
        
        before_label, before = self.snapshots[-2]
        after_label, after = self.snapshots[-1]
        expected = before - bet_amount
        diff = abs(after - expected)
        
        print(f"\n余额扣减验证:")
        print(f"  {before_label}: {before}")
        print(f"  投注金额: {bet_amount}")
        print(f"  期望余额: {expected}")
        print(f"  {after_label}: {after}")
        print(f"  差异: {diff}")
        
        assert diff < tolerance, \
            f"余额扣减不正确: {before_label}={before}, {after_label}={after}, " \
            f"期望={expected}, 差异={diff}"
        
        print("✓ 余额扣减验证通过")
    
    def verify_settlement(self, pnl: float, tolerance: float = 0.01) -> None:
        """
        验证结算后余额更新是否正确
        
        Args:
            pnl: 盈亏金额（正数表示盈利，负数表示亏损）
            tolerance: 允许的误差范围（默认0.01）
            
        Raises:
            AssertionError: 余额更新不正确时抛出
        """
        if len(self.snapshots) < 2:
            raise ValueError("需要至少2个快照才能验证结算")
        
        before_label, before = self.snapshots[-2]
        after_label, after = self.snapshots[-1]
        expected = before + pnl
        diff = abs(after - expected)
        
        print(f"\n结算余额验证:")
        print(f"  {before_label}: {before}")
        print(f"  盈亏: {pnl}")
        print(f"  期望余额: {expected}")
        print(f"  {after_label}: {after}")
        print(f"  差异: {diff}")
        
        assert diff < tolerance, \
            f"结算余额不正确: {before_label}={before}, {after_label}={after}, " \
            f"期望={expected}, 差异={diff}"
        
        print("✓ 结算余额验证通过")
    
    def get_balance_history(self) -> List[Tuple[str, float]]:
        """
        获取所有余额快照历史
        
        Returns:
            余额快照列表 [(标签, 余额), ...]
        """
        return list(self.snapshots)
    
    def print_history(self) -> None:
        """打印余额变化历史"""
        print("\n余额变化历史:")
        for i, (label, balance) in enumerate(self.snapshots):
            if i > 0:
                prev_balance = self.snapshots[i-1][1]
                change = balance - prev_balance
                change_str = f"({change:+.2f})" if change != 0 else ""
                print(f"  {i}. {label}: {balance} {change_str}")
            else:
                print(f"  {i}. {label}: {balance}")
