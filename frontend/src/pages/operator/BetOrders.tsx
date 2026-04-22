import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';

import { listAccounts } from '@/api/accounts';
import { listBetOrders, type ListBetOrdersParams } from '@/api/bet-orders';
import { isApiError } from '@/api/request';
import type { BetOrderInfo } from '@/types/api/bet-order';
import type { AccountInfo } from '@/types/api/account';

import './BetOrders.css';

type BetOrderLedger = 'real' | 'simulation';

interface OrderSummary {
  total_amount: number;
  total_payout: number;
}

interface FiltersState {
  strategyId: string;
  accountId: string;
  status: string;
  dateFrom: string;
  dateTo: string;
}

const DEFAULT_FILTERS: FiltersState = {
  strategyId: '',
  accountId: '',
  status: '',
  dateFrom: '',
  dateTo: '',
};

const PAGE_SIZE = 50;

function formatCurrency(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--';
  return value.toFixed(2);
}

function formatStatus(status: string): string {
  switch (status) {
    case 'pending':
      return '待下注';
    case 'bet_success':
      return '下注成功';
    case 'pending_match':
      return '待结算';
    case 'settling':
      return '结算中';
    case 'settled':
      return '已结算';
    case 'bet_failed':
      return '下注失败';
    default:
      return status;
  }
}

function getStatusClass(status: string): string {
  if (status === 'settled') return 'status-tag status-settled';
  if (status === 'bet_failed') return 'status-tag status-failed';
  return 'status-tag status-pending';
}

function getLedgerTitle(ledger: BetOrderLedger): string {
  return ledger === 'simulation' ? '模拟投注记录' : '真实投注记录';
}

function getLedgerEmptyText(ledger: BetOrderLedger): string {
  return ledger === 'simulation'
    ? '暂无模拟投注记录。'
    : '暂无真实投注记录。';
}

function getLedgerSummaryLabel(ledger: BetOrderLedger): string {
  return ledger === 'simulation' ? '模拟汇总' : '真实汇总';
}

function buildParams(
  page: number,
  ledger: BetOrderLedger,
  filters: FiltersState,
): ListBetOrdersParams {
  return {
    page,
    page_size: PAGE_SIZE,
    ledger,
    strategy_id: filters.strategyId ? Number(filters.strategyId) : undefined,
    account_id: filters.accountId ? Number(filters.accountId) : undefined,
    status: filters.status || undefined,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
  };
}

export default function BetOrders() {
  const [ledger, setLedger] = useState<BetOrderLedger>('real');
  const [filters, setFilters] = useState<FiltersState>(DEFAULT_FILTERS);
  const [draftFilters, setDraftFilters] = useState<FiltersState>(DEFAULT_FILTERS);
  const [orders, setOrders] = useState<BetOrderInfo[]>([]);
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [summary, setSummary] = useState<OrderSummary>({ total_amount: 0, total_payout: 0 });
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  const loadAccounts = useCallback(async () => {
    const response = await listAccounts();
    setAccounts(response.data ?? []);
  }, []);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await listBetOrders(buildParams(page, ledger, filters));
      const data = response.data;
      if (!data) {
        setOrders([]);
        setTotal(0);
        setSummary({ total_amount: 0, total_payout: 0 });
        return;
      }
      setOrders(data.paged.items ?? []);
      setTotal(data.paged.total ?? 0);
      setSummary(data.summary ?? { total_amount: 0, total_payout: 0 });
    } catch (err) {
      setError(isApiError(err) ? err.message : '加载投注记录失败。');
      setOrders([]);
      setTotal(0);
      setSummary({ total_amount: 0, total_payout: 0 });
    } finally {
      setLoading(false);
    }
  }, [filters, ledger, page]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPage(1);
    setFilters(draftFilters);
  };

  const handleReset = () => {
    setDraftFilters(DEFAULT_FILTERS);
    setFilters(DEFAULT_FILTERS);
    setPage(1);
  };

  const handleLedgerSwitch = (nextLedger: BetOrderLedger) => {
    if (nextLedger === ledger) return;
    setLedger(nextLedger);
    setPage(1);
  };

  const refresh = () => {
    void loadOrders();
  };

  return (
    <div className="bet-orders-page">
      <div className="bet-orders-header">
        <div>
          <h2 className="bet-orders-title">{getLedgerTitle(ledger)}</h2>
          <p className="bet-orders-subtitle">{getLedgerSummaryLabel(ledger)}</p>
        </div>
        <button type="button" className="refresh-btn" onClick={refresh}>
          刷新
        </button>
      </div>

      <div className="bet-orders-ledger-switch" role="tablist" aria-label="投注记录账本切换">
        <button
          type="button"
          role="tab"
          aria-selected={ledger === 'real'}
          className={`ledger-tab${ledger === 'real' ? ' ledger-tab-active' : ''}`}
          onClick={() => handleLedgerSwitch('real')}
        >
          真实
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={ledger === 'simulation'}
          className={`ledger-tab${ledger === 'simulation' ? ' ledger-tab-active' : ''}`}
          onClick={() => handleLedgerSwitch('simulation')}
        >
          模拟
        </button>
      </div>

      <form className="bet-orders-filters" onSubmit={handleSubmit}>
        <label className="filter-field">
          <span className="filter-label">账号</span>
          <select
            className="filter-input"
            value={draftFilters.accountId}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, accountId: event.target.value }))
            }
          >
            <option value="">全部账号</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.account_name}
              </option>
            ))}
          </select>
        </label>

        <label className="filter-field">
          <span className="filter-label">策略编号</span>
          <input
            className="filter-input"
            value={draftFilters.strategyId}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, strategyId: event.target.value }))
            }
            placeholder="不限"
          />
        </label>

        <label className="filter-field">
          <span className="filter-label">状态</span>
          <select
            className="filter-input"
            value={draftFilters.status}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, status: event.target.value }))
            }
          >
            <option value="">全部状态</option>
            <option value="pending">待下注</option>
            <option value="settled">已结算</option>
          </select>
        </label>

        <label className="filter-field">
          <span className="filter-label">开始日期</span>
          <input
            className="filter-input"
            type="date"
            value={draftFilters.dateFrom}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, dateFrom: event.target.value }))
            }
          />
        </label>

        <label className="filter-field">
          <span className="filter-label">结束日期</span>
          <input
            className="filter-input"
            type="date"
            value={draftFilters.dateTo}
            onChange={(event) =>
              setDraftFilters((prev) => ({ ...prev, dateTo: event.target.value }))
            }
          />
        </label>

        <button type="submit" className="page-btn">
          应用筛选
        </button>
        <button type="button" className="filter-reset-btn" onClick={handleReset}>
          重置
        </button>
      </form>

      {error ? <div className="bet-orders-error">{error}</div> : null}

      {loading ? <div className="loading-text">正在加载投注记录...</div> : null}

      {!loading && !error && orders.length === 0 ? (
        <div className="empty-text">{getLedgerEmptyText(ledger)}</div>
      ) : null}

      {!loading && !error && orders.length > 0 ? (
        <>
          <div className="orders-table-wrap">
            <table className="orders-table">
              <thead>
                <tr>
                  <th>期号</th>
                  <th>策略</th>
                  <th>账号</th>
                  <th>玩法</th>
                  <th>金额</th>
                  <th>状态</th>
                  <th>盈亏</th>
                  <th>下注时间</th>
                  <th>失败原因</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr
                    key={`${ledger}-${order.id}`}
                    className={order.status === 'bet_failed' ? 'row-fail' : undefined}
                  >
                    <td>{order.issue}</td>
                    <td>{order.strategy_name || order.strategy_id}</td>
                    <td>{order.account_name || order.account_id}</td>
                    <td title={order.key_code}>{order.key_code_name || order.key_code}</td>
                    <td>{formatCurrency(order.amount)}</td>
                    <td>
                      <span className={getStatusClass(order.status)}>
                        {formatStatus(order.status)}
                      </span>
                    </td>
                    <td className={typeof order.pnl === 'number' && order.pnl < 0 ? 'pnl-negative' : 'pnl-positive'}>
                      {formatCurrency(order.pnl)}
                    </td>
                    <td className="td-time">{order.bet_at || '--'}</td>
                    <td className="td-bet-content" title={order.fail_reason || ''}>
                      {order.fail_reason || '--'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="orders-summary">
            <span>{getLedgerSummaryLabel(ledger)}</span>
            <span>总投注额：{formatCurrency(summary.total_amount)}</span>
            <span>总派彩：{formatCurrency(summary.total_payout)}</span>
          </div>

          <div className="bet-orders-pagination">
            <button
              type="button"
              className="page-btn"
              disabled={page <= 1}
              onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            >
              上一页
            </button>
            <span className="page-info">
              第 {page} 页 / 共 {totalPages} 页
            </span>
            <button
              type="button"
              className="page-btn"
              disabled={page >= totalPages}
              onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
            >
              下一页
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
