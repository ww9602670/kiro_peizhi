import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Strategies from './Strategies';

const { mockIsApiError, mockShowToast } = vi.hoisted(() => ({
  mockIsApiError: vi.fn(() => false),
  mockShowToast: vi.fn(),
}));

vi.mock('@/api/strategies', () => ({
  listStrategies: vi.fn(),
  updateStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  startStrategy: vi.fn(),
  pauseStrategy: vi.fn(),
  stopStrategy: vi.fn(),
}));

vi.mock('@/api/request', () => ({
  isApiError: mockIsApiError,
}));

vi.mock('@/components/ConfirmDialog', () => ({
  default: () => null,
}));

vi.mock('@/components/Toast', () => ({
  default: () => null,
}));

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => ({
    confirmState: { open: false, message: '', title: '' },
    confirm: vi.fn().mockResolvedValue(true),
    handleConfirm: vi.fn(),
    handleCancel: vi.fn(),
  }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({
    messages: [],
    showToast: mockShowToast,
    removeToast: vi.fn(),
  }),
}));

vi.mock('./StrategyForm', () => ({
  default: (props: { initialAccountId?: number }) => (
    <div data-testid="strategy-form">account:{props.initialAccountId ?? 'empty'}</div>
  ),
}));

vi.mock('./BetOrders', () => ({
  default: () => <div>投注记录页</div>,
}));

vi.mock('./Backtest', () => ({
  default: () => <div>回测页</div>,
}));

vi.mock('@/components/CountdownDisplay', () => ({
  CountdownDisplay: () => <div data-testid="countdown">countdown</div>,
}));

import { listStrategies, startStrategy, updateStrategy } from '@/api/strategies';

const mockListStrategies = vi.mocked(listStrategies);
const mockStartStrategy = vi.mocked(startStrategy);
const mockUpdateStrategy = vi.mocked(updateStrategy);

beforeEach(() => {
  vi.clearAllMocks();
  mockIsApiError.mockReturnValue(false);
  mockListStrategies.mockResolvedValue({
    code: 0,
    message: 'success',
    data: [
      {
        id: 1,
        account_id: 1,
        name: '策略 A',
        type: 'flat',
        play_code: 'DX1',
        play_code_name: '单双',
        base_amount: 10,
        martin_sequence: null,
        bet_timing: 30,
        simulation: false,
        status: 'stopped',
        martin_level: 0,
        stop_loss: null,
        take_profit: null,
        daily_pnl: 12,
        total_pnl: 18,
        platform_type: 'JND28WEB',
      },
    ],
  });
  mockStartStrategy.mockResolvedValue({ code: 0, message: 'success', data: null as never });
  mockUpdateStrategy.mockResolvedValue({ code: 0, message: 'success', data: null as never });
});

describe('Strategies', () => {
  it('opens create form with the intended account and consumes the intent', async () => {
    const onConsumed = vi.fn();

    render(
      <Strategies
        createIntent={{ accountId: 42, nonce: 1 }}
        onCreateIntentConsumed={onConsumed}
      />,
    );

    expect(await screen.findByTestId('strategy-form')).toHaveTextContent('account:42');
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1));
  });

  it('renders countdown summary on the strategies page', async () => {
    render(<Strategies />);
    expect(await screen.findByTestId('countdown')).toBeInTheDocument();
  });

  it('switches between list, bet orders, and backtest workspaces', async () => {
    const user = userEvent.setup();
    render(<Strategies />);

    expect(await screen.findByText('策略 A')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '投注记录' }));
    expect(screen.getByText('投注记录页')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: '回测' }));
    expect(screen.getByText('回测页')).toBeInTheDocument();
  });

  it('renders simulation strategy pnl values from api response', async () => {
    mockListStrategies.mockResolvedValueOnce({
      code: 0,
      message: 'success',
      data: [
        {
          id: 2,
          account_id: 2,
          name: 'Sim Strategy',
          type: 'flat',
          play_code: 'DX1',
          play_code_name: 'DX',
          base_amount: 10,
          martin_sequence: null,
          bet_timing: 30,
          simulation: true,
          status: 'stopped',
          martin_level: 0,
          stop_loss: null,
          take_profit: null,
          daily_pnl: 123.45,
          total_pnl: -67.89,
          platform_type: 'JND28WEB',
        },
      ],
    });

    render(<Strategies />);

    expect(await screen.findByText('Sim Strategy')).toBeInTheDocument();
    expect(screen.getByText(/\+123\.45/)).toBeInTheDocument();
    expect(screen.getByText(/-67\.89/)).toBeInTheDocument();
  });

  it('renders strategy mode toggle on the list and calls updateStrategy', async () => {
    const user = userEvent.setup();
    render(<Strategies />);

    expect(await screen.findByText('当前模式：')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '切到模拟' }));

    await waitFor(() => {
      expect(mockUpdateStrategy).toHaveBeenCalledWith(1, { simulation: true });
    });
  });

  it.skip('merges rapid start clicks into one request', async () => {
    const user = userEvent.setup();
    let releaseStart: (() => void) | undefined;
    mockStartStrategy.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseStart = () => resolve({ code: 0, message: 'success', data: null as never });
        }) as never
    );

    render(<Strategies />);

    const startButton = await screen.findByRole('button', { name: '鍚姩' });
    await Promise.all([user.click(startButton), user.click(startButton)]);
    expect(mockStartStrategy).toHaveBeenCalledTimes(1);
    releaseStart?.();
  });

  it.skip('converts session-like errors into operator guidance', async () => {
    const user = userEvent.setup();
    mockIsApiError.mockReturnValue(true);
    mockStartStrategy.mockRejectedValue({ code: 500, message: 'worker session expired' });

    render(<Strategies />);

    await user.click(await screen.findByRole('button', { name: '鍚姩' }));

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('需要人工处理：请前往账号页重新登录后再试。');
    });
  });
});
