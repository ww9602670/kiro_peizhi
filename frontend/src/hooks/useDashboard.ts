/**
 * 操作者仪表盘 Hook
 * - 加载仪表盘数据
 * - 30s 自动刷新余额
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchDashboard } from '@/api/dashboard';
import { isApiError } from '@/api/request';
import type { OperatorDashboard } from '@/types/api/dashboard';

const REFRESH_INTERVAL_MS = 30_000;

export function useDashboard() {
  const [data, setData] = useState<OperatorDashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetchDashboard();
      if (res.data) setData(res.data);
    } catch (err) {
      if (isApiError(err)) {
        setError(err.message);
      } else {
        setError('加载仪表盘失败');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const startAutoRefresh = useCallback(() => {
    load();
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(load, REFRESH_INTERVAL_MS);
  }, [load]);

  const stopAutoRefresh = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return { data, loading, error, reload: load, startAutoRefresh, stopAutoRefresh };
}
