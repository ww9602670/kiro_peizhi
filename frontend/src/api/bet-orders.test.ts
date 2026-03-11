import { describe, expect, it, vi, beforeEach } from 'vitest';
import { listBetOrders, getBetOrder } from './bet-orders';

// Mock request
vi.mock('@/api/request', () => ({
  request: vi.fn(),
  isApiError: vi.fn(),
}));

import { request } from '@/api/request';
const mockRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('listBetOrders', () => {
  it('calls /bet-orders with no params', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { items: [], total: 0, page: 1, page_size: 50 } });
    await listBetOrders();
    expect(mockRequest).toHaveBeenCalledWith('/bet-orders');
  });

  it('builds query string with all params', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { items: [], total: 0, page: 1, page_size: 20 } });
    await listBetOrders({ page: 2, page_size: 20, date_from: '2026-01-01', date_to: '2026-01-31', strategy_id: 5 });
    expect(mockRequest).toHaveBeenCalledWith(
      '/bet-orders?page=2&page_size=20&date_from=2026-01-01&date_to=2026-01-31&strategy_id=5',
    );
  });
});

describe('getBetOrder', () => {
  it('calls /bet-orders/:id', async () => {
    mockRequest.mockResolvedValue({ code: 0, message: 'success', data: { id: 1 } });
    await getBetOrder(1);
    expect(mockRequest).toHaveBeenCalledWith('/bet-orders/1');
  });
});
