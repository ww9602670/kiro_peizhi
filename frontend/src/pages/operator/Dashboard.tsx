/**
 * 操作者仪表盘页面
 * - 余额卡片 + 当日盈亏 + 总盈亏
 * - 运行中策略列表
 * - 最近 20 条投注
 * - 未读告警数
 * - 彩票倒计时显示
 */

import { useEffect } from 'react';
import { useDashboard } from '@/hooks/useDashboard';
import BetOrderTable from '@/components/BetOrderTable';
import { CountdownDisplay } from '@/components/CountdownDisplay';
import './Dashboard.css';

export default function Dashboard() {
  const { data, loading, error, startAutoRefresh, stopAutoRefresh } = useDashboard();

  useEffect(() => {
    startAutoRefresh();
    return () => stopAutoRefresh();
  }, [startAutoRefresh, stopAutoRefresh]);

  if (loading && !data) {
    return <div className="dashboard-page"><p className="loading-text">加载中...</p></div>;
  }

  if (error && !data) {
    return <div className="dashboard-page"><p className="error-text">{error}</p></div>;
  }

  if (!data) return null;

  return (
    <div className="dashboard-page">
      <h1 className="dashboard-title">仪表盘</h1>

      {/* 彩票倒计时 */}
      <div className="countdown-section">
        <CountdownDisplay />
      </div>

      {/* 统计卡片 */}
      <div className="stat-cards">
        <div className="stat-card">
          <span className="stat-label">总余额</span>
          <span className="stat-value">{data.balance.toFixed(2)}</span>
          <span className="stat-unit">元</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">当日盈亏</span>
          <span className={`stat-value ${data.daily_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
            {data.daily_pnl > 0 ? '+' : ''}{data.daily_pnl.toFixed(2)}
          </span>
          <span className="stat-unit">元</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">总盈亏</span>
          <span className={`stat-value ${data.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
            {data.total_pnl > 0 ? '+' : ''}{data.total_pnl.toFixed(2)}
          </span>
          <span className="stat-unit">元</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">未读告警</span>
          <span className="stat-value stat-alert">{data.unread_alerts}</span>
        </div>
      </div>

      {/* 运行中策略 */}
      <section className="dashboard-section">
        <h2 className="section-title">运行中策略 ({data.running_strategies.length})</h2>
        {data.running_strategies.length > 0 ? (
          <div className="strategy-list">
            {data.running_strategies.map((s) => (
              <div key={s.id} className="strategy-card-mini">
                <span className="strategy-name">{s.name}</span>
                <span className="strategy-type">{s.type === 'flat' ? '平注' : '马丁'}</span>
                <span className={`strategy-pnl ${s.daily_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                  今日 {s.daily_pnl > 0 ? '+' : ''}{s.daily_pnl.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="empty-text">暂无运行中策略</p>
        )}
      </section>

      {/* 最近投注 */}
      <section className="dashboard-section">
        <h2 className="section-title">最近投注</h2>
        <BetOrderTable orders={data.recent_bets} />
      </section>
    </div>
  );
}
