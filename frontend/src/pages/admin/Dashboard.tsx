/**
 * 管理员仪表盘页面
 * - 操作者汇总表格
 * - 系统统计
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchAdminDashboard } from '@/api/admin';
import { isApiError } from '@/api/request';
import type { AdminDashboard } from '@/types/api/dashboard';
import './AdminDashboard.css';

export default function AdminDashboardPage() {
  const [data, setData] = useState<AdminDashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetchAdminDashboard();
      if (res.data) setData(res.data);
    } catch (err) {
      if (isApiError(err)) setError(err.message);
      else setError('加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <div className="admin-dashboard"><p className="loading-text">加载中...</p></div>;
  if (error && !data) return <div className="admin-dashboard"><p className="error-text">{error}</p></div>;
  if (!data) return null;

  return (
    <div className="admin-dashboard">
      <h1 className="admin-title">管理员仪表盘</h1>

      <div className="admin-stat-cards">
        <div className="admin-stat-card">
          <span className="admin-stat-label">总操作者</span>
          <span className="admin-stat-value">{data.total_operators}</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">活跃操作者</span>
          <span className="admin-stat-value">{data.active_operators}</span>
        </div>
      </div>

      <section className="admin-section">
        <h2 className="admin-section-title">操作者汇总</h2>
        {data.operator_summaries.length > 0 ? (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>用户名</th>
                  <th>状态</th>
                  <th>当日盈亏</th>
                  <th>总盈亏</th>
                  <th>运行策略</th>
                </tr>
              </thead>
              <tbody>
                {data.operator_summaries.map((op) => (
                  <tr key={op.id}>
                    <td>{op.id}</td>
                    <td>{op.username}</td>
                    <td>
                      <span className={`op-status op-status-${op.status}`}>
                        {op.status === 'active' ? '活跃' : op.status === 'disabled' ? '禁用' : op.status}
                      </span>
                    </td>
                    <td className={op.daily_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}>
                      {op.daily_pnl > 0 ? '+' : ''}{op.daily_pnl.toFixed(2)}
                    </td>
                    <td className={op.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}>
                      {op.total_pnl > 0 ? '+' : ''}{op.total_pnl.toFixed(2)}
                    </td>
                    <td>{op.running_strategies}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-text">暂无操作者</p>
        )}
      </section>
    </div>
  );
}
