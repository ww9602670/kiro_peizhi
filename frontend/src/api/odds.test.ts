import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';
import { confirmAccountOdds, getAccountOdds, refreshAccountOdds } from './odds';

const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
  mockRequest.mockResolvedValue({ code: 0, message: 'success', data: null });
});

describe('odds api', () => {
  it('includes platform_type when loading odds', async () => {
    await getAccountOdds(12, 'JND28WEB');

    expect(mockRequest).toHaveBeenCalledWith('/accounts/12/odds?platform_type=JND28WEB');
  });

  it('includes platform_type when confirming odds', async () => {
    await confirmAccountOdds(12, 'JND282');

    expect(mockRequest).toHaveBeenCalledWith('/accounts/12/odds/confirm?platform_type=JND282', {
      method: 'POST',
    });
  });

  it('includes platform_type when refreshing odds', async () => {
    await refreshAccountOdds(12, 'LUCKYSB');

    expect(mockRequest).toHaveBeenCalledWith('/accounts/12/odds/refresh?platform_type=LUCKYSB', {
      method: 'POST',
    });
  });
});
