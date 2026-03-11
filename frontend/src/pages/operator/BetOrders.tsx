/**
 * 投注记录页面
 * - 分页列表
 * - 日期筛选 + 策略筛选
 * - 异常高亮（bet_failed 行红色背景）
 */

import { useCallback, useEffect, useState } from 'react';
import { listBetOrders, type ListBetOrdersParams } from '@/api/bet-orders';
import { isApiError } from '@/api/request';
import BetOrderTable from '@/components/BetOrderTable';
import type { BetOrderInfo } from '@/types/api/bet-order';
import './BetOrders.css';

export default function BetOrders() {
  const [orders, setOrders] = useState<BetOrderInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Filters
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [strategyId, setStrategyId] = useState('');

  const pageSize = 50;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params: ListBetOrdersParams = { page, page_size: pageSize };
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      if (strategyId) params.strategy_id = Number(strategyId);
      const res = await listBetOrders(params);
      if (res.data) {
        setOrders(res.data.items);
        setTotal(res.data.total);
      }
    } catch (err) {
      if (isApiError(err)) {
        setError(err.message);
      } else {
        setError('加载投注记录失败');
      }
    } finally {
      setLoading(false);
    }
  }, [page, dateFrom, dateTo, strategyId]);

  useEffect(() => { load(); }, [load]);

  const handleReset = () => {
    setDateFrom('');
    setDateTo('');
    setStrategyId('');
    setPage(1);
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="bet-orders-page">
      <div className="bet-orders-header">
        <h1 className="bet-orders-title">投注记录</h1>
        <button type="button" className="refresh-btn" onClick={load}>刷新</button>
      </div>

      {/* 筛选栏 */}
      <div className="bet-orders-filters">
        <label className="filter-field">
          <span className="filter-label">起始日期</span>
          <input
            type="date"
            className="filter-input"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
          />
        </label>
        <label className="filter-field">
          <span className="filter-label">结束日期</span>
          <input
            type="date"
            className="filter-input"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
          />
        </label>
        <label className="filter-field">
          <span className="filter-label">策略ID</span>
          <input
            type="number"
            className="filter-input"
            placeholder="全部"
            value={strategyId}
            onChange={(e) => { setStrategyId(e.target.value); setPage(1); }}
          />
        </label>
        <button type="button" className="filter-reset-btn" onClick={handleReset}>重置</button>
      </div>

      {error && <div className="bet-orders-error" role="alert">{error}</div>}

      {loading ? (
        <p className="loading-text">加载中...</p>
      ) : (
        <>
          <BetOrderTable orders={orders} />
          {totalPages > 1 && (
            <div className="bet-orders-pagination">
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
