/**
 * 投注记录页面
 * - 日期筛选（默认当天）+ 状态筛选 + 账户筛选 + 策略ID
 * - 紧凑表格：状态 | 期号 | 投注内容 | 金额 | 派彩 | 时间
 * - 底部统计行 + 分页
 */

import { useCallback, useEffect, useState } from 'react';
import { listBetOrders, type ListBetOrdersParams, type BetOrdersResponse } from '@/api/bet-orders';
import { listAccounts } from '@/api/accounts';
import { isApiError } from '@/api/request';
import type { BetOrderInfo } from '@/types/api/bet-order';
import type { AccountInfo } from '@/types/api/account';
import './BetOrders.css';

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const STATUS_LABEL: Record<string, string> = {
  bet_success: '待结',
  settling: '待结',
  pending_match: '待结',
  settled: '已结',
};

function shortStatus(s: string): string {
  return STATUS_LABEL[s] ?? s;
}

function isPending(s: string): boolean {
  return ['bet_success', 'settling', 'pending_match'].includes(s);
}

export default function BetOrders() {
  const [orders, setOrders] = useState<BetOrderInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [summary, setSummary] = useState({ total_amount: 0, total_payout: 0 });

  // Filters
  const [dateFrom, setDateFrom] = useState(todayStr);
  const [dateTo, setDateTo] = useState(todayStr);
  const [statusFilter, setStatusFilter] = useState('');
  const [accountId, setAccountId] = useState('');
  const [strategyId, setStrategyId] = useState('');

  // 账户列表
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);

  const pageSize = 50;

  // 加载账户列表
  useEffect(() => {
    listAccounts().then((res) => {
      if (res.data) setAccounts(res.data);
    }).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params: ListBetOrdersParams = { page, page_size: pageSize };
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      if (strategyId) params.strategy_id = Number(strategyId);
      if (statusFilter) params.status = statusFilter;
      if (accountId) params.account_id = Number(accountId);
      const res = await listBetOrders(params);
      if (res.data) {
        const d = res.data as BetOrdersResponse;
        setOrders(d.paged.items);
        setTotal(d.paged.total);
        setSummary(d.summary);
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
  }, [page, dateFrom, dateTo, strategyId, statusFilter, accountId]);

  useEffect(() => { load(); }, [load]);

  const handleReset = () => {
    setDateFrom(todayStr());
    setDateTo(todayStr());
    setStatusFilter('');
    setAccountId('');
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
          <span className="filter-label">开始日期</span>
          <input type="date" className="filter-input" value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} />
        </label>
        <label className="filter-field">
          <span className="filter-label">结束日期</span>
          <input type="date" className="filter-input" value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setPage(1); }} />
        </label>
        <label className="filter-field">
          <span className="filter-label">状态</span>
          <select className="filter-input" value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}>
            <option value="">全部</option>
            <option value="settled">已结算</option>
            <option value="pending">待结算</option>
          </select>
        </label>
        <label className="filter-field">
          <span className="filter-label">账户</span>
          <select className="filter-input" value={accountId}
            onChange={(e) => { setAccountId(e.target.value); setPage(1); }}>
            <option value="">全部</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.account_name}</option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span className="filter-label">策略ID</span>
          <input type="number" className="filter-input" placeholder="全部" value={strategyId}
            onChange={(e) => { setStrategyId(e.target.value); setPage(1); }} />
        </label>
        <button type="button" className="filter-reset-btn" onClick={handleReset}>重置</button>
      </div>

      {error && <div className="bet-orders-error" role="alert">{error}</div>}

      {loading ? (
        <p className="loading-text">加载中...</p>
      ) : (
        <>
          {orders.length === 0 ? (
            <p className="empty-text">暂无投注记录</p>
          ) : (
            <div className="orders-table-wrap">
              <table className="orders-table">
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>期号</th>
                    <th>投注内容</th>
                    <th>金额</th>
                    <th>派彩</th>
                    <th>时间</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => (
                    <tr key={o.id} className={o.status === 'bet_failed' ? 'row-fail' : ''}>
                      <td>
                        <span className={`status-tag ${isPending(o.status) ? 'status-pending' : 'status-settled'}`}>
                          {shortStatus(o.status)}
                        </span>
                      </td>
                      <td>{o.issue}</td>
                      <td className="td-bet-content" title={o.key_code_name}>
                        {o.key_code_name.length > 6 ? o.key_code_name.slice(0, 6) + '…' : o.key_code_name}
                      </td>
                      <td>{Math.round(o.amount)}</td>
                      <td className={o.payout != null && o.pnl != null ? (o.pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : ''}>
                        {o.payout != null ? Math.round(o.payout) : '-'}
                      </td>
                      <td className="td-time">{o.bet_at ? o.bet_at.slice(5, 16) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 底部统计 */}
          <div className="orders-summary">
            <span>共 {total} 条</span>
            <span>总投注 ¥{summary.total_amount.toFixed(2)}</span>
            <span>总派彩 ¥{summary.total_payout.toFixed(2)}</span>
          </div>

          {totalPages > 1 && (
            <div className="bet-orders-pagination">
              <button type="button" className="page-btn" disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}>上一页</button>
              <span className="page-info">{page} / {totalPages}</span>
              <button type="button" className="page-btn" disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}>下一页</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
