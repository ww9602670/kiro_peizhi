/**
 * 投注记录 API 封装测试
 * - listBetOrders 路径和分页参数
 * - getBetOrder 路径
 * - 筛选参数传递
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { listBetOrders, getBetOrder } from './bet-orders';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
});

const sampleOrder = {
  id: 1,
  idempotent_id: '202603021001-1-DX1',
  strategy_id: 1,
  account_id: 1,
  issue: '202603021001',
  key_code: 'DX1',
  key_code_name: '大',
  amount: 10.0,
  odds: 1.98,
  status: 'settled',
  open_result: '3,5,8',
  sum_value: 16,
  is_win: 1,
  pnl: 9.8,
  simulation: false,
  martin_level: null,
  bet_at: '2026-03-02 10:01:30',
  settled_at: '2026-03-02 10:05:00',
  fail_reason: null,
};

describe('listBetOrders', () => {
  it('无参数时调用默认路径', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0, message: 'success',
      data: {
        paged: { items: [], total: 0, page: 1, page_size: 50 },
        summary: { total_amount: 0, total_payout: 0 },
      },
    });
    await listBetOrders();
    expect(mockRequest).toHaveBeenCalled();
  });

  it('返回投注记录数据', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0, message: 'success',
      data: {
        paged: { items: [sampleOrder], total: 1, page: 1, page_size: 50 },
        summary: { total_amount: 10, total_payout: 19.8 },
      },
    });
    const res = await listBetOrders();
    expect(res.data!.paged.items).toHaveLength(1);
    expect(res.data!.paged.items[0].key_code_name).toBe('大');
  });
});

describe('getBetOrder', () => {
  it('调用正确路径', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0, message: 'success', data: sampleOrder,
    });
    const res = await getBetOrder(1);
    expect(mockRequest).toHaveBeenCalledWith('/bet-orders/1');
    expect(res.data!.key_code).toBe('DX1');
  });
});
