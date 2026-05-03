import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CountdownDisplay } from './CountdownDisplay';
import { DrawStateEnum, LotteryStateEnum, MarketDataStateEnum } from '@/types/api/lottery';

vi.mock('@/hooks/useLotteryCountdown', () => ({
  useLotteryCountdown: vi.fn(),
}));

import { useLotteryCountdown } from '@/hooks/useLotteryCountdown';

const mockUseLotteryCountdown = vi.mocked(useLotteryCountdown);

function mockCountdownResult(overrides?: Record<string, unknown>) {
  return {
    data: {
      installments: '20260419018',
      state: LotteryStateEnum.OPEN,
      close_countdown_sec: 25,
      open_countdown_sec: 42,
      pre_lottery_result: '1,2,0',
      pre_installments: '20260419017',
      template_code: 'JNDPCDD',
      market_data_state: MarketDataStateEnum.SHARED_HIT,
      ...((overrides?.data as object) ?? {}),
    },
    closeCountdown: 25,
    openCountdown: 42,
    closeTimestamp: 25,
    openTimestamp: 42,
    error: null,
    lastUpdateTime: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, 'alert').mockImplementation(() => {});
});

describe('CountdownDisplay', () => {
  it('renders current issue, countdowns and previous result balls', () => {
    mockUseLotteryCountdown.mockReturnValue(mockCountdownResult());

    render(<CountdownDisplay />);

    expect(screen.getByText('20260419017')).toBeInTheDocument();
    expect(screen.getByText('20260419018')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getAllByText('0').length).toBeGreaterThan(0);
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(window.alert).not.toHaveBeenCalled();
  });

  it('passes explicit platformType into the hook', () => {
    mockUseLotteryCountdown.mockReturnValue(mockCountdownResult({ data: null }));

    render(<CountdownDisplay platformType="JND282" />);

    expect(mockUseLotteryCountdown).toHaveBeenCalledWith({ platformType: 'JND282' });
  });

  it('shows closed state when close countdown reaches zero while draw countdown continues', () => {
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          close_countdown_sec: 0,
          open_countdown_sec: 35,
        },
        closeCountdown: 0,
        openCountdown: 35,
      }),
    );

    render(<CountdownDisplay />);

    expect(screen.getByText('封盘中')).toBeInTheDocument();
  });

  it('shows waiting state when both countdowns are zero', () => {
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          close_countdown_sec: 0,
          open_countdown_sec: 0,
        },
        closeCountdown: 0,
        openCountdown: 0,
      }),
    );

    render(<CountdownDisplay />);

    expect(screen.getByText('等待开奖')).toBeInTheDocument();
  });

  it('shows market closed text when market_data_state is market_closed', () => {
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          market_data_state: MarketDataStateEnum.MARKET_CLOSED,
        },
      }),
    );

    render(<CountdownDisplay />);

    expect(screen.getByText('当前处于停盘')).toBeInTheDocument();
  });

  it('shows draw_wait_retry notice message', () => {
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          draw_state: DrawStateEnum.DRAW_WAIT_RETRY,
        },
      }),
    );

    render(<CountdownDisplay />);

    expect(screen.getByText('开奖数据暂未获取，10秒后再次刷新')).toBeInTheDocument();
  });

  it('shows SHARED-002 popup once for shared_error state', () => {
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          market_data_state: MarketDataStateEnum.SHARED_ERROR,
        },
      }),
    );

    const { rerender } = render(<CountdownDisplay />);
    rerender(<CountdownDisplay />);

    expect(window.alert).toHaveBeenCalledTimes(1);
    expect(window.alert).toHaveBeenCalledWith(
      '数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。',
    );
  });

  it('shows SHARED-002 popup with backend message text when message code is SHARED-002', () => {
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          market_data_state: MarketDataStateEnum.SHARED_OK,
          message_code: 'SHARED-002',
          message_text: '数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。',
        },
      }),
    );

    render(<CountdownDisplay />);

    expect(window.alert).toHaveBeenCalledTimes(1);
    expect(window.alert).toHaveBeenCalledWith(
      '数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。',
    );
  });

  it('renders recent results and expands on demand', () => {
    mockUseLotteryCountdown.mockReturnValue(mockCountdownResult());

    render(
      <CountdownDisplay
        recentResults={[1, 2, 3, 4].map((id) => ({
          id,
          issue: `2026041909${id}`,
          open_result: '1,2,3',
          sum_value: 6,
          open_time: `2026-04-19 00:0${id}:00`,
          created_at: `2026-04-19 00:0${id}:10`,
        }))}
      />,
    );

    expect(screen.getByLabelText('recent-results')).toBeInTheDocument();
    expect(screen.getByText('20260419091')).toBeInTheDocument();
    expect(screen.queryByText('20260419094')).not.toBeInTheDocument();
    expect(screen.getAllByText('和值 6').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /展开全部/ }));
    expect(screen.getByText('20260419094')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '收起' }));
    expect(screen.queryByText('20260419094')).not.toBeInTheDocument();
  });

  it('shows error banner and fallback when previous result is missing', () => {
    const lastUpdateTime = new Date('2026-04-19T11:00:00');
    mockUseLotteryCountdown.mockReturnValue(
      mockCountdownResult({
        data: {
          installments: '',
          state: LotteryStateEnum.UNKNOWN,
          close_countdown_sec: 0,
          open_countdown_sec: 0,
          pre_lottery_result: '',
          pre_installments: '',
          template_code: '',
          market_data_state: MarketDataStateEnum.PROCESSING,
        },
        closeCountdown: 0,
        openCountdown: 0,
        error: 'countdown failed',
        lastUpdateTime,
      }),
    );

    render(<CountdownDisplay />);

    expect(screen.getByText(/countdown failed/)).toBeInTheDocument();
    expect(screen.getByText(/更新时间/)).toBeInTheDocument();
    expect(screen.getByText('暂无结果')).toBeInTheDocument();
  });
});
