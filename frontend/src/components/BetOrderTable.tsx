/**
 * 投注记录表格组件（复用于仪表盘和投注记录页）
 */

import type { BetOrderInfo } from '@/types/api/bet-order';
import './BetOrderTable.css';

const STATUS_LABEL: Record<string, string> = {
  pending: '待下注',
  betting: '下注中',
  bet_success: '已下注',
  bet_failed: '下注失败',
  settling: '结算中',
  settled: '已结算',
  pending_match: '待匹配',
  settle_timeout: '结算超时',
  settle_failed: '结算失败',
  reconcile_error: '对账异常',
  cancelled: '已取消',
};

interface BetOrderTableProps {
  orders: BetOrderInfo[];
}

export default function BetOrderTable({ orders }: BetOrderTableProps) {
  if (orders.length === 0) {
    return <p className="empty-text">暂无投注记录</p>;
  }

  return (
    <div className="bet-order-table-wrap">
      <table className="bet-order-table">
        <thead>
          <tr>
            <th>期号</th>
            <th>玩法</th>
            <th>金额</th>
            <th>赔率</th>
            <th>状态</th>
            <th>开奖</th>
            <th>盈亏</th>
            <th>模拟</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr
              key={o.id}
              className={
                o.status === 'bet_failed'
                  ? 'row-fail'
                  : o.is_win === 1
                    ? 'row-win'
                    : o.is_win === 0
                      ? 'row-lose'
                      : ''
              }
            >
              <td>{o.issue}</td>
              <td>{o.key_code_name}</td>
              <td>{o.amount.toFixed(2)}</td>
              <td>{o.odds != null ? o.odds.toFixed(2) : '-'}</td>
              <td>
                <span className={`order-status order-status-${o.status}`}>
                  {STATUS_LABEL[o.status] ?? o.status}
                </span>
              </td>
              <td>{o.open_result ?? '-'}</td>
              <td className={o.pnl != null ? (o.pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : ''}>
                {o.pnl != null ? (o.pnl > 0 ? `+${o.pnl.toFixed(2)}` : o.pnl.toFixed(2)) : '-'}
              </td>
              <td>{o.simulation ? '是' : '否'}</td>
              <td className="td-time">{o.bet_at ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
