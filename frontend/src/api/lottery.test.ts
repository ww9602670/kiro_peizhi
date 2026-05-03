import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchCurrentInstall } from './lottery';
import {
  DrawStateEnum,
  LotteryStateEnum,
  MarketDataStateEnum,
} from '@/types/api/lottery';
import {
  __resetLotteryCountdownStoresForTest,
  useLotteryCountdown,
} from '@/hooks/useLotteryCountdown';

vi.mock('@/api/request', () => ({
  request: vi.fn(),
}));

import { request } from '@/api/request';

const mockRequest = vi.mocked(request);

beforeEach(() => {
  mockRequest.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  __resetLotteryCountdownStoresForTest();
  vi.useRealTimers();
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

  it('normalizes remediation status fields', async () => {
    mockRequest.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: {
        installments: '20260503001',
        state: LotteryStateEnum.CLOSED,
        close_countdown_sec: 0,
        open_countdown_sec: 0,
        market_data_state: 'market_closed',
        draw_state: 'draw_wait_retry',
        next_normal_refresh_at: '2026-05-03T12:00:25Z',
        next_draw_retry_at: '2026-05-03T12:00:10Z',
        snapshot_version: '17',
        message_code: 'SHARED-002',
        message_text: '数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。',
      },
    });

    const response = await fetchCurrentInstall();

    expect(response.data).toMatchObject({
      market_data_state: MarketDataStateEnum.MARKET_CLOSED,
      draw_state: DrawStateEnum.DRAW_WAIT_RETRY,
      next_normal_refresh_at: '2026-05-03T12:00:25Z',
      next_draw_retry_at: '2026-05-03T12:00:10Z',
      snapshot_version: 17,
      message_code: 'SHARED-002',
      message_text: '数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。',
    });
  });
});

describe('useLotteryCountdown polling rhythm', () => {
  afterEach(() => {
    vi.clearAllTimers();
  });

  it('triggers first refresh 10 seconds after draw countdown reaches zero', async () => {
    mockRequest
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '20260503001',
          state: LotteryStateEnum.CLOSED,
          close_countdown_sec: 0,
          open_countdown_sec: 1,
          pre_lottery_result: '1,2,3',
          pre_installments: '20260503000',
          template_code: 'JND282',
          market_data_state: 'shared_ok',
          draw_state: 'normal',
        },
      })
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '20260503002',
          state: LotteryStateEnum.OPEN,
          close_countdown_sec: 120,
          open_countdown_sec: 180,
          pre_lottery_result: '2,3,4',
          pre_installments: '20260503001',
          template_code: 'JND282',
          market_data_state: 'shared_ok',
          draw_state: 'normal',
        },
      });

    const { result } = renderHook(() => useLotteryCountdown({ platformType: 'JND282' }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(mockRequest).toHaveBeenCalledTimes(1);
    expect(result.current.openCountdown).toBe(1);

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.openCountdown).toBe(0);
    expect(mockRequest).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(9999);
      await Promise.resolve();
    });
    expect(mockRequest).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(mockRequest).toHaveBeenCalledTimes(2);
    expect(result.current.data?.installments).toBe('20260503002');
  });

  it('uses 10 seconds fallback polling in draw_wait_retry without next_draw_retry_at', async () => {
    mockRequest
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '20260503001',
          state: LotteryStateEnum.CLOSED,
          close_countdown_sec: 0,
          open_countdown_sec: 0,
          pre_lottery_result: '1,2,3',
          pre_installments: '20260503000',
          template_code: 'JND282',
          market_data_state: 'shared_ok',
          draw_state: 'draw_wait_retry',
        },
      })
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '20260503002',
          state: LotteryStateEnum.OPEN,
          close_countdown_sec: 110,
          open_countdown_sec: 170,
          pre_lottery_result: '2,3,4',
          pre_installments: '20260503001',
          template_code: 'JND282',
          market_data_state: 'shared_ok',
          draw_state: 'normal',
        },
      });

    renderHook(() => useLotteryCountdown());

    await act(async () => {
      await Promise.resolve();
    });
    expect(mockRequest).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(9999);
      await Promise.resolve();
    });
    expect(mockRequest).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(mockRequest).toHaveBeenCalledTimes(2);
  });
});
