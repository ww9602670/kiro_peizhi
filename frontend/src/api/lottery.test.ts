import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchCurrentInstall } from './lottery';
import { LotteryStateEnum } from '@/types/api/lottery';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';

const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
});

describe('fetchCurrentInstall', () => {
  it('requests endpoint with normalized platform_type', async () => {
    mockRequest.mockResolvedValueOnce({ code: 0, message: 'success', data: null });

    await fetchCurrentInstall('jnd282');

    expect(mockRequest).toHaveBeenCalledWith(
      '/lottery/current-install?platform_type=JND282',
    );
  });

  it('normalizes legacy fields to CurrentInstall', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: {
        issue: 3403606,
        state: 99,
        close_timestamp: '-3',
        open_timestamp: '15',
        pre_issue: 3403605,
        pre_result: null,
        templateCode: 'PCDD',
      },
    });

    const response = await fetchCurrentInstall();

    expect(response.data).toEqual({
      installments: '3403606',
      state: LotteryStateEnum.UNKNOWN,
      close_countdown_sec: 0,
      open_countdown_sec: 15,
      pre_lottery_result: '',
      pre_installments: '3403605',
      template_code: 'PCDD',
    });
  });
});
