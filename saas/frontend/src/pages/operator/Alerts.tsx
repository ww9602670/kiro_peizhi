/**
 * 告警列表页面（Mobile-First）
 * - 告警列表（卡片布局）
 * - 筛选：全部 / 未读 / 已读
 * - 标记单条已读
 * - 全部已读
 */

import { useCallback, useEffect, useState } from 'react';
import { isApiError } from '@/api/request';
import { useAlerts } from '@/hooks/useAlerts';
import type { ListAlertsParams } from '@/api/alerts';
import './Alerts.css';

type FilterTab = 'all' | 'unread' | 'read';

const LEVEL_CLASS: Record<string, string> = {
  critical: 'alert-level-critical',
  warning: 'alert-level-warning',
  info: 'alert-level-info',
};

const LEVEL_LABEL: Record<string, string> = {
  critical: '严重',
  warning: '警告',
  info: '信息',
};

export default function Alerts() {
  const {
    alerts,
    total,
    loading,
    error,
    fetchAlerts,
    markRead,
    markAllRead,
    fetchUnreadCount,
  } = useAlerts();

  const [filter, setFilter] = useState<FilterTab>('all');
  const [page, setPage] = useState(1);
  const [actionError, setActionError] = useState('');
  const pageSize = 20;

  const loadAlerts = useCallback(() => {
    const params: ListAlertsParams = { page, page_size: pageSize };
    if (filter === 'unread') params.is_read = 0;
    if (filter === 'read') params.is_read = 1;
    fetchAlerts(params);
  }, [fetchAlerts, filter, page, pageSize]);

  useEffect(() => {
    loadAlerts();
  }, [loadAlerts]);

  const handleFilterChange = (tab: FilterTab) => {
    setFilter(tab);
    setPage(1);
  };

  const handleMarkRead = async (alertId: number) => {
    setActionError('');
    try {
      await markRead(alertId);
      fetchUnreadCount();
    } catch (err) {
      if (isApiError(err)) {
        setActionError(err.message);
      } else {
        setActionError('标记已读失败');
      }
    }
  };

  const handleMarkAllRead = async () => {
    setActionError('');
    try {
      await markAllRead();
      fetchUnreadCount();
      loadAlerts();
    } catch (err) {
      if (isApiError(err)) {
        setActionError(err.message);
      } else {
        setActionError('全部已读失败');
      }
    }
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="alerts-page">
      <div className="alerts-header">
        <h1 className="alerts-title">告警中心</h1>
        <button
          type="button"
          className="alerts-mark-all-btn"
          onClick={handleMarkAllRead}
        >
          全部已读
        </button>
      </div>

      {/* Filter tabs */}
      <div className="alerts-filter" role="tablist">
        {(['all', 'unread', 'read'] as FilterTab[]).map((tab) => (
          <button
            key={tab}
            role="tab"
            type="button"
            aria-selected={filter === tab}
            className={`filter-btn ${filter === tab ? 'filter-btn-active' : ''}`}
            onClick={() => handleFilterChange(tab)}
          >
            {tab === 'all' ? '全部' : tab === 'unread' ? '未读' : '已读'}
          </button>
        ))}
      </div>

      {actionError && (
        <div className="alerts-action-error" role="alert">{actionError}</div>
      )}

      {loading && <div className="alerts-loading">加载中...</div>}

      {!loading && error && (
        <div className="alerts-error" role="alert">{error}</div>
      )}

      {!loading && !error && alerts.length === 0 && (
        <div className="alerts-empty">暂无告警</div>
      )}

      {!loading && !error && alerts.length > 0 && (
        <>
          <div className="alert-list">
            {alerts.map((alert) => (
              <div
                key={alert.id}
                className={`alert-card ${alert.is_read ? 'alert-card-read' : 'alert-card-unread'}`}
              >
                <div className="alert-card-header">
                  <span className={`alert-level ${LEVEL_CLASS[alert.level] ?? 'alert-level-info'}`}>
                    {LEVEL_LABEL[alert.level] ?? alert.level}
                  </span>
                  <span className="alert-time">{alert.created_at}</span>
                </div>
                <div className="alert-card-body">
                  <h3 className="alert-card-title">{alert.title}</h3>
                  {alert.detail && (
                    <p className="alert-card-detail">{alert.detail}</p>
                  )}
                </div>
                {!alert.is_read && (
                  <div className="alert-card-actions">
                    <button
                      type="button"
                      className="alert-read-btn"
                      onClick={() => handleMarkRead(alert.id)}
                    >
                      标记已读
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="alerts-pagination">
              <button
                type="button"
                className="page-btn"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                上一页
              </button>
              <span className="page-info">{page} / {totalPages}</span>
              <button
                type="button"
                className="page-btn"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
