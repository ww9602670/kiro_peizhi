import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CountdownDisplay } from './CountdownDisplay';
import { LotteryStateEnum } from '@/types/api/lottery';

vi.mock('@/hooks/useLotteryCountdown', () => ({
  useLotteryCountdown: vi.fn(),
}));

import { useLotteryCountdown } from '@/hooks/useLotteryCountdown';

const mockUseLotteryCountdown = vi.mocked(useLotteryCountdown);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CountdownDisplay', () => {
  it('renders the current issue, countdowns, state label, and previous result balls', () => {
    mockUseLotteryCountdown.mockReturnValue({
      data: {
        installments: '20260419018',
        state: LotteryStateEnum.OPEN,
        close_countdown_sec: 25,
        open_countdown_sec: 42,
        pre_lottery_result: '1,2,0',
        pre_installments: '20260419017',
        template_code: 'JNDPCDD',
        market_data_state: 'shared_hit',
      },
      closeCountdown: 25,
      openCountdown: 42,
      closeTimestamp: 25,
      openTimestamp: 42,
      error: null,
      lastUpdateTime: null,
    });

    render(<CountdownDisplay />);

    expect(screen.getByText('20260419017')).toBeInTheDocument();
    expect(screen.getByText('20260419018')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getAllByText('0').length).toBeGreaterThan(0);
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('passes explicit platformType into the hook', () => {
    mockUseLotteryCountdown.mockReturnValue({
      data: null,
      closeCountdown: 0,
      openCountdown: 0,
      closeTimestamp: 0,
      openTimestamp: 0,
      error: null,
      lastUpdateTime: null,
    });

    render(<CountdownDisplay platformType="JND282" />);

    expect(mockUseLotteryCountdown).toHaveBeenCalledWith({ platformType: 'JND282' });
  });

  it('renders recent results as mobile-friendly cards with full issue numbers', () => {
    mockUseLotteryCountdown.mockReturnValue({
      data: {
        installments: '20260419099',
        state: LotteryStateEnum.OPEN,
        close_countdown_sec: 10,
        open_countdown_sec: 20,
        pre_lottery_result: '3,3,3',
        pre_installments: '20260419098',
        template_code: 'JND282',
        market_data_state: 'shared_hit',
      },
      closeCountdown: 10,
      openCountdown: 20,
      closeTimestamp: 10,
      openTimestamp: 20,
      error: null,
      lastUpdateTime: null,
    });

    render(
      <CountdownDisplay
        platformType="JND282"
        recentResults={[
          {
            id: 1,
            issue: '20260419097',
            open_result: '1,2,3',
            sum_value: 6,
            open_time: '2026-04-19 00:01:00',
            created_at: '2026-04-19 00:01:10',
          },
        ]}
      />,
    );

    expect(screen.getByLabelText('recent-results')).toBeInTheDocument();
    expect(screen.getByText('20260419097')).toBeInTheDocument();
    expect(screen.getByText('和值 6')).toBeInTheDocument();
    expect(screen.getByText('2026-04-19 00:01:00')).toBeInTheDocument();
  });

  it('shows an error banner and graceful fallback when previous result is missing', () => {
    const lastUpdateTime = new Date('2026-04-19T11:00:00');
    mockUseLotteryCountdown.mockReturnValue({
      data: {
        installments: '',
        state: LotteryStateEnum.UNKNOWN,
        close_countdown_sec: 0,
        open_countdown_sec: 0,
        pre_lottery_result: '',
        pre_installments: '',
        template_code: '',
        market_data_state: 'processing',
      },
      closeCountdown: 0,
      openCountdown: 0,
      closeTimestamp: 0,
      openTimestamp: 0,
      error: 'countdown failed',
      lastUpdateTime,
    });

    render(<CountdownDisplay />);

    expect(screen.getByText(/countdown failed/)).toBeInTheDocument();
    expect(screen.getByText(/更新时间/)).toBeInTheDocument();
    expect(screen.getByText('暂无结果')).toBeInTheDocument();
  });
});
