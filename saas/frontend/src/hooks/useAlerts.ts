/**
 * 告警管理 Hook
 * - 未读数量轮询（15s 间隔）
 * - 告警列表加载（分页）
 * - 标记已读 / 全部已读
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import * as alertsApi from '@/api/alerts';
import type { ListAlertsParams } from '@/api/alerts';
import type { AlertInfo } from '@/types/api/alert';
import { isApiError } from '@/api/request';

const POLL_INTERVAL_MS = 15_000;

export function useAlerts() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [alerts, setAlerts] = useState<AlertInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchUnreadCount = useCallback(async () => {
    try {
      const res = await alertsApi.getUnreadCount();
      if (res.data) {
        setUnreadCount(res.data.count);
      }
    } catch {
      // 轮询失败静默忽略
    }
  }, []);

  const fetchAlerts = useCallback(async (params: ListAlertsParams = {}) => {
    setLoading(true);
    setError('');
    try {
      const res = await alertsApi.listAlerts(params);
      if (res.data) {
        setAlerts(res.data.items);
        setTotal(res.data.total);
      }
    } catch (err) {
      if (isApiError(err)) {
        setError(err.message);
      } else {
        setError('加载告警列表失败');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const markRead = useCallback(async (alertId: number) => {
    try {
      await alertsApi.markAlertRead(alertId);
      setAlerts((prev) =>
        prev.map((a) => (a.id === alertId ? { ...a, is_read: 1 } : a)),
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch (err) {
      if (isApiError(err)) {
        throw err;
      }
      throw err;
    }
  }, []);

  const markAllRead = useCallback(async () => {
    try {
      await alertsApi.markAllAlertsRead();
      setAlerts((prev) => prev.map((a) => ({ ...a, is_read: 1 })));
      setUnreadCount(0);
    } catch (err) {
      if (isApiError(err)) {
        throw err;
      }
      throw err;
    }
  }, []);

  // 启动未读数量轮询
  const startPolling = useCallback(() => {
    fetchUnreadCount();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(fetchUnreadCount, POLL_INTERVAL_MS);
  }, [fetchUnreadCount]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  return {
    unreadCount,
    alerts,
    total,
    loading,
    error,
    fetchAlerts,
    fetchUnreadCount,
    markRead,
    markAllRead,
    startPolling,
    stopPolling,
  };
}
