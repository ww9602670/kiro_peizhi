import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import BetOrders from './BetOrders';

vi.mock('@/api/bet-orders', () => ({
  listBetOrders: vi.fn(),
}));

vi.mock('@/api/accounts', () => ({
  listAccounts: vi.fn(),
}));

vi.mock('@/api/request', () => ({
  isApiError: vi.fn(() => false),
}));

import { listAccounts } from '@/api/accounts';
import { listBetOrders } from '@/api/bet-orders';

const mockListAccounts = vi.mocked(listAccounts);
const mockListBetOrders = vi.mocked(listBetOrders);

beforeEach(() => {
  vi.clearAllMocks();

  mockListAccounts.mockResolvedValue({
    code: 0,
    message: 'success',
    data: [],
  });
  mockListBetOrders.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      paged: { items: [], total: 0, page: 1, page_size: 50 },
      summary: { total_amount: 0, total_payout: 0 },
    },
  });
});

describe('BetOrders', () => {
  it('loads real ledger by default', async () => {
    render(<BetOrders />);

    await waitFor(() => {
      expect(mockListBetOrders).toHaveBeenCalled();
    });

    const firstCall = mockListBetOrders.mock.calls[0]?.[0];
    expect(firstCall).toEqual(expect.objectContaining({ ledger: 'real' }));
  });

  it('switches to simulation ledger', async () => {
    const user = userEvent.setup();
    render(<BetOrders />);

    await waitFor(() => {
      expect(mockListBetOrders).toHaveBeenCalled();
    });

    await user.click(screen.getByRole('tab', { name: '模拟' }));

    await waitFor(() => {
      expect(mockListBetOrders).toHaveBeenLastCalledWith(
        expect.objectContaining({ ledger: 'simulation' }),
      );
    });

  });
});
