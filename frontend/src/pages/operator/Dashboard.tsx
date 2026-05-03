import { useEffect, useMemo, useState } from 'react';
import { CountdownDisplay } from '@/components/CountdownDisplay';
import { useAlertsContext } from '@/hooks/useAlertsContext';
import { operatorUpdates } from '@/data/operatorUpdates';
import { useAuth } from '@/hooks/useAuth';
import { useDashboard } from '@/hooks/useDashboard';
import { getPlatformLabel } from '@/utils/platformLabels';
import { getPlayCodeDisplay } from '@/utils/playCodeDisplay';
import './Dashboard.css';

interface DashboardProps {
  onCreateStrategy?: () => void;
}

function shouldShowAlertDetail(title: string, detail: string | null): boolean {
  if (!detail) return false;
  return !title.includes('日志编号：');
}

function readDismissed(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function getStrategyTypeLabel(type: string): string {
  if (type === 'flat') return '普通';
  if (type === 'martin') return '马丁';
  if (type === 'omission_random_flat') return '遗漏随机平注';
  if (type === 'omission_random_martin') return '遗漏随机马丁';
  if (type === 'ai_random_flat') return 'AI推荐平注';
  if (type === 'ai_random_martin') return 'AI推荐马丁';
  if (type === 'red_wave_double_martin') return '红波双马丁';
  if (type === 'green_wave_single_martin') return '绿波追单';
  return type;
}

export default function Dashboard({ onCreateStrategy }: DashboardProps) {
  const { data, loading, error, startAutoRefresh, stopAutoRefresh } = useDashboard();
  const { operatorId } = useAuth();
  const {
    alerts,
    initialized: alertsInitialized,
    unreadCount,
    fetchAlerts,
    markRead,
  } = useAlertsContext();

  const lsKey = `operator_dismissed_updates_${operatorId ?? 'anon'}`;
  const [dismissedUpdateIds, setDismissedUpdateIds] = useState<string[]>(() => readDismissed(lsKey));
  const [isUpdatesOpen, setIsUpdatesOpen] = useState(false);
  const [runningOpen, setRunningOpen] = useState(false);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [closingId, setClosingId] = useState<number | null>(null);
  const [closeError, setCloseError] = useState('');

  useEffect(() => {
    startAutoRefresh();
    return () => stopAutoRefresh();
  }, [startAutoRefresh, stopAutoRefresh]);

  useEffect(() => {
    fetchAlerts({ page: 1, page_size: 10 });
  }, [fetchAlerts]);

  useEffect(() => {
    setDismissedUpdateIds(readDismissed(lsKey));
  }, [lsKey]);

  useEffect(() => {
    if (!closeError) return;
    const t = setTimeout(() => setCloseError(''), 3000);
    return () => clearTimeout(t);
  }, [closeError]);

  const recentUpdates = useMemo(
    () => operatorUpdates.filter((u) => !dismissedUpdateIds.includes(u.id)).slice(0, 2),
    [dismissedUpdateIds],
  );

  const visibleAlerts = useMemo(
    () => alerts.filter((a) => !a.is_read).slice(0, 5),
    [alerts],
  );

  const dismissUpdate = (id: string) => {
    const next = [...dismissedUpdateIds, id];
    setDismissedUpdateIds(next);
    try {
      localStorage.setItem(lsKey, JSON.stringify(next));
    } catch {
      // 存储失败不影响视觉状态
    }
  };

  const handleCloseAlert = async (alertId: number) => {
    setClosingId(alertId);
    setCloseError('');
    try {
      await markRead(alertId);
    } catch {
      setCloseError('关闭失败，请重试');
    } finally {
      setClosingId(null);
    }
  };

  if (loading && !data) {
    return <div className="dashboard-page"><p className="loading-text">加载中...</p></div>;
  }

  if (error && !data) {
    return <div className="dashboard-page"><p className="error-text">{error}</p></div>;
  }

  if (!data) {
    return null;
  }

  const showAlertSection = !alertsInitialized || visibleAlerts.length > 0;
  const runningCount = data.running_strategies.length;
  const pendingCount = data.pending_bets.length;
  const countdownPlatform = data.countdown_platform_type;

  return (
    <div className="dashboard-page">
      <div className="dashboard-hero">
        <div>
          <h1 className="dashboard-title">仪表盘</h1>
          <p className="dashboard-subtitle">集中查看开奖、告警、平台更新和当前运行情况。</p>
        </div>
        {onCreateStrategy && (
          <button type="button" className="dashboard-primary-btn" onClick={onCreateStrategy}>
            + 创建策略
          </button>
        )}
      </div>

      <div className="countdown-section">
        <CountdownDisplay platformType={countdownPlatform} recentResults={data.recent_results?.slice(0, 10)} />
      </div>

      {recentUpdates.length > 0 && (
        <section className="dashboard-section">
          <div className="section-title-row">
            <h2 className="section-title">平台更新</h2>
            <button type="button" className="section-link-btn" onClick={() => setIsUpdatesOpen(true)}>
              查看全部
            </button>
          </div>
          <div className="update-list">
            {recentUpdates.map((entry) => (
              <article key={entry.id} className="update-card">
                <button
                  type="button"
                  className="update-close"
                  aria-label="关闭此更新"
                  onClick={() => dismissUpdate(entry.id)}
                >
                  ×
                </button>
                <div className="update-date">{entry.date}</div>
                <h3 className="update-title">{entry.title}</h3>
                <ul className="update-items">
                  {entry.items.map((item) => (
                    <li key={item} className="update-item">
                      {item}
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </section>
      )}

      <div className="stat-cards">
        <div className="stat-card">
          <span className="stat-label">余额</span>
          <span className="stat-value">{data.balance.toFixed(2)}</span>
          <span className="stat-unit">元</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">今日盈亏</span>
          <span className={`stat-value ${data.daily_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
            {data.daily_pnl > 0 ? '+' : ''}{data.daily_pnl.toFixed(2)}
          </span>
          <span className="stat-unit">元</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">累计盈亏</span>
          <span className={`stat-value ${data.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
            {data.total_pnl > 0 ? '+' : ''}{data.total_pnl.toFixed(2)}
          </span>
          <span className="stat-unit">元</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">未读告警</span>
          <span className="stat-value stat-alert">{unreadCount}</span>
        </div>
      </div>

      {showAlertSection && (
        <section className="dashboard-section">
          <div className="section-title-row">
            <h2 className="section-title">告警看板</h2>
            {closeError ? (
              <span className="alert-close-error">{closeError}</span>
            ) : (
              <span className="section-meta">关闭即标记为已读</span>
            )}
          </div>
          {!alertsInitialized ? (
            <div className="alert-skeleton" aria-hidden="true">
              <div className="alert-skeleton-row" />
              <div className="alert-skeleton-row" />
            </div>
          ) : (
            <div className="alert-list-compact">
              {visibleAlerts.map((alert) => (
                <article key={alert.id} className={`alert-card-compact alert-level-${alert.level}`}>
                  <div className="alert-card-compact-header">
                    <strong>{alert.title}</strong>
                    <button
                      type="button"
                      className="alert-close-btn"
                      disabled={closingId === alert.id}
                      onClick={() => handleCloseAlert(alert.id)}
                    >
                      {closingId === alert.id ? '关闭中…' : '关闭'}
                    </button>
                  </div>
                  {shouldShowAlertDetail(alert.title, alert.detail) && (
                    <p className="alert-card-compact-detail">{alert.detail}</p>
                  )}
                  <p className="alert-card-compact-time">{alert.created_at}</p>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="dashboard-section">
        <button
          type="button"
          className="section-toggle"
          aria-expanded={runningOpen}
          onClick={() => setRunningOpen((v) => !v)}
        >
          <span className="section-toggle-icon">{runningOpen ? '▾' : '▸'}</span>
          <span className="section-title">运行中的策略</span>
          <span className="section-toggle-count">{runningCount}</span>
        </button>
        <div className={`coll-body${runningOpen ? ' open' : ''}`}>
          {runningCount > 0 ? (
            <div className="strategy-list">
              {data.running_strategies.map((strategy) => (
                <div key={strategy.id} className="strategy-card-mini">
                  <span className="strategy-account">{strategy.account_name ?? '-'}</span>
                  <span className="strategy-platform">{getPlatformLabel(strategy.platform_type)}</span>
                  <span className="strategy-name">{strategy.name}</span>
                  <span className="strategy-play">
                    {getPlayCodeDisplay(strategy.play_code_name, strategy.play_code)}
                  </span>
                  <span className="strategy-type">
                    {getStrategyTypeLabel(strategy.type)}
                  </span>
                  <span className={`strategy-pnl ${strategy.daily_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                    今日 {strategy.daily_pnl > 0 ? '+' : ''}{strategy.daily_pnl.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-text">暂无运行中的策略。</p>
          )}
        </div>
      </section>

      <section className="dashboard-section">
        <button
          type="button"
          className="section-toggle"
          aria-expanded={pendingOpen}
          onClick={() => setPendingOpen((v) => !v)}
        >
          <span className="section-toggle-icon">{pendingOpen ? '▾' : '▸'}</span>
          <span className="section-title">待结算订单</span>
          <span className="section-toggle-count">{pendingCount}</span>
        </button>
        <div className={`coll-body${pendingOpen ? ' open' : ''}`}>
          {pendingCount > 0 ? (
            <div className="pending-table-wrap">
              <table className="pending-table">
                <thead>
                  <tr>
                    <th>期号</th>
                    <th>账号</th>
                    <th>策略</th>
                    <th>投注内容</th>
                    <th>金额</th>
                    <th>时间</th>
                  </tr>
                </thead>
                <tbody>
                  {data.pending_bets.map((order) => (
                    <tr key={order.id}>
                      <td>{order.issue}</td>
                      <td>{order.account_name ?? '-'}</td>
                      <td>{order.strategy_name ?? '-'}</td>
                      <td className="td-bet-content" title={order.key_code_name}>
                        {order.key_code_name.length > 6 ? `${order.key_code_name.slice(0, 6)}…` : order.key_code_name}
                      </td>
                      <td>{Math.round(order.amount)}</td>
                      <td className="td-time">{order.bet_at ? order.bet_at.slice(5, 16) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty-text">暂无待结算订单。</p>
          )}
        </div>
      </section>

      {isUpdatesOpen && (
        <div className="updates-modal-mask" role="presentation" onClick={() => setIsUpdatesOpen(false)}>
          <div
            className="updates-modal"
            role="dialog"
            aria-modal="true"
            aria-label="平台更新详情"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="updates-modal-header">
              <h2 className="section-title">平台更新详情</h2>
              <button type="button" className="section-link-btn" onClick={() => setIsUpdatesOpen(false)}>
                关闭
              </button>
            </div>
            <div className="updates-modal-body">
              {operatorUpdates.map((entry) => (
                <article key={entry.id} className="update-card">
                  <div className="update-date">{entry.date}</div>
                  <h3 className="update-title">{entry.title}</h3>
                  <ul className="update-items">
                    {entry.items.map((item) => (
                      <li key={item} className="update-item">
                        {item}
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
