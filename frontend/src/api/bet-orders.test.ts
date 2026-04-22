import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getBetOrder, listBetOrders } from './bet-orders';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';

const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
});

describe('listBetOrders', () => {
  it('uses base path when params are empty', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: {
        paged: { items: [], total: 0, page: 1, page_size: 50 },
        summary: { total_amount: 0, total_payout: 0 },
      },
    });

    await listBetOrders();

    expect(mockRequest).toHaveBeenCalledWith('/bet-orders');
  });

  it('includes ledger in query string', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: {
        paged: { items: [], total: 0, page: 1, page_size: 50 },
        summary: { total_amount: 0, total_payout: 0 },
      },
    });

    await listBetOrders({ ledger: 'simulation', page: 2 });

    expect(mockRequest).toHaveBeenCalledWith('/bet-orders?page=2&ledger=simulation');
  });
});

describe('getBetOrder', () => {
  it('calls detail endpoint with default real ledger', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { id: 1 },
    });

    await getBetOrder(1);

    expect(mockRequest).toHaveBeenCalledWith('/bet-orders/1?ledger=real');
  });

  it('supports simulation ledger detail requests', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: { id: 2 },
    });

    await getBetOrder(2, 'simulation');

    expect(mockRequest).toHaveBeenCalledWith('/bet-orders/2?ledger=simulation');
  });
});
