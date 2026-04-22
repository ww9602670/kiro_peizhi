import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetLotteryCountdownStoresForTest, useLotteryCountdown } from './useLotteryCountdown';
import { fetchCurrentInstall } from '@/api/lottery';
import { LotteryStateEnum } from '@/types/api/lottery';

vi.mock('@/api/lottery', () => ({
  fetchCurrentInstall: vi.fn(),
}));

const mockFetchCurrentInstall = vi.mocked(fetchCurrentInstall);

beforeEach(() => {
  mockFetchCurrentInstall.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  __resetLotteryCountdownStoresForTest();
  vi.useRealTimers();
});

describe('useLotteryCountdown', () => {
  afterEach(() => {
    vi.clearAllTimers();
  });

  it('loads initial countdown and clamps at zero while ticking', async () => {
    mockFetchCurrentInstall.mockResolvedValue({
      code: 0,
      message: 'success',
      data: {
        installments: '3403606',
        state: LotteryStateEnum.OPEN,
        close_countdown_sec: 2,
        open_countdown_sec: 1,
        pre_lottery_result: '1,2,3',
        pre_installments: '3403605',
        template_code: 'JNDPCDD',
      },
    });

    const { result } = renderHook(() => useLotteryCountdown());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.closeCountdown).toBe(2);
    expect(result.current.openCountdown).toBe(1);

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(result.current.closeCountdown).toBe(0);
    expect(result.current.openCountdown).toBe(0);
    expect(result.current.closeTimestamp).toBe(0);
    expect(result.current.openTimestamp).toBe(0);
  });

  it('does not refetch every 5 seconds while local countdown is progressing', async () => {
    mockFetchCurrentInstall.mockResolvedValue({
      code: 0,
      message: 'success',
      data: {
        installments: '3403606',
        state: LotteryStateEnum.OPEN,
        close_countdown_sec: 30,
        open_countdown_sec: 40,
        pre_lottery_result: '1,2,3',
        pre_installments: '3403605',
        template_code: 'JNDPCDD',
      },
    });

    renderHook(() => useLotteryCountdown({ platformType: 'JND282' }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(1);
    expect(mockFetchCurrentInstall).toHaveBeenLastCalledWith('JND282');

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(1);
  });

  it('refetches after draw countdown reaches zero and the 30-second wait passes', async () => {
    mockFetchCurrentInstall
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3403606',
          state: LotteryStateEnum.CLOSED,
          close_countdown_sec: 0,
          open_countdown_sec: 2,
          pre_lottery_result: '1,2,3',
          pre_installments: '3403605',
          template_code: 'JNDPCDD',
        },
      })
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3403607',
          state: LotteryStateEnum.OPEN,
          close_countdown_sec: 145,
          open_countdown_sec: 155,
          pre_lottery_result: '4,5,6',
          pre_installments: '3403606',
          template_code: 'JNDPCDD',
        },
      });

    const { result } = renderHook(() => useLotteryCountdown());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.data?.installments).toBe('3403606');
    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(result.current.openCountdown).toBe(0);
    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(30000);
      await Promise.resolve();
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(2);
    expect(result.current.data?.installments).toBe('3403607');
  });

  it('keeps the last snapshot visible when refresh fails and retries after 5 seconds', async () => {
    mockFetchCurrentInstall
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3403606',
          state: LotteryStateEnum.CLOSED,
          close_countdown_sec: 0,
          open_countdown_sec: 1,
          pre_lottery_result: '1,2,3',
          pre_installments: '3403605',
          template_code: 'JNDPCDD',
        },
      })
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3403607',
          state: LotteryStateEnum.OPEN,
          close_countdown_sec: 100,
          open_countdown_sec: 110,
          pre_lottery_result: '4,5,6',
          pre_installments: '3403606',
          template_code: 'JNDPCDD',
        },
      });

    const { result } = renderHook(() => useLotteryCountdown());

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    await act(async () => {
      vi.advanceTimersByTime(30000);
      await Promise.resolve();
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(2);
    expect(result.current.data?.installments).toBe('3403606');
    expect(result.current.error).toBe('数据延迟');

    await act(async () => {
      vi.advanceTimersByTime(5000);
      await Promise.resolve();
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(3);
    expect(result.current.data?.installments).toBe('3403607');
  });

  it('schedules delayed refresh when first usable snapshot already has zero countdowns', async () => {
    mockFetchCurrentInstall
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3403606',
          state: LotteryStateEnum.CLOSED,
          close_countdown_sec: 0,
          open_countdown_sec: 0,
          pre_lottery_result: '1,2,3',
          pre_installments: '3403605',
          template_code: 'JNDPCDD',
        },
      })
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3403607',
          state: LotteryStateEnum.OPEN,
          close_countdown_sec: 145,
          open_countdown_sec: 155,
          pre_lottery_result: '4,5,6',
          pre_installments: '3403606',
          template_code: 'JNDPCDD',
        },
      });

    const { result } = renderHook(() => useLotteryCountdown({ platformType: 'JND282' }));

    await act(async () => {
      await Promise.resolve();
    });

    // First snapshot is usable (has installments) but both countdowns are already zero.
    expect(result.current.data?.installments).toBe('3403606');
    expect(result.current.openCountdown).toBe(0);
    // Should NOT have fetched again yet
    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(1);

    // After 30s delayed refresh fires, hook should fetch and advance to next issue.
    await act(async () => {
      vi.advanceTimersByTime(30000);
      await Promise.resolve();
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(2);
    expect(result.current.data?.installments).toBe('3403607');
  });

  it('retries after 5 seconds when the initial snapshot is empty', async () => {
    mockFetchCurrentInstall
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '',
          state: LotteryStateEnum.UNKNOWN,
          close_countdown_sec: 0,
          open_countdown_sec: 0,
          pre_lottery_result: '',
          pre_installments: '',
          template_code: '',
        },
      })
      .mockResolvedValueOnce({
        code: 0,
        message: 'success',
        data: {
          installments: '3423130',
          state: LotteryStateEnum.OPEN,
          close_countdown_sec: 120,
          open_countdown_sec: 150,
          pre_lottery_result: '1,2,3',
          pre_installments: '3423129',
          template_code: 'JNDPCDD',
        },
      });

    const { result } = renderHook(() => useLotteryCountdown({ platformType: 'JND282' }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(1);
    expect(result.current.data?.installments).toBe('');

    await act(async () => {
      vi.advanceTimersByTime(5000);
      await Promise.resolve();
    });

    expect(mockFetchCurrentInstall).toHaveBeenCalledTimes(2);
    expect(result.current.data?.installments).toBe('3423130');
    expect(result.current.closeCountdown).toBe(120);
  });
});
