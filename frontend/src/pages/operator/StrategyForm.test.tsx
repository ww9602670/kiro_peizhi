import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StrategyForm from './StrategyForm';

vi.mock('@/api/accounts', () => ({
  listAccounts: vi.fn(),
}));

vi.mock('@/api/strategies', () => ({
  createStrategy: vi.fn(),
  updateStrategy: vi.fn(),
}));

vi.mock('@/api/request', () => ({
  isApiError: () => false,
}));

vi.mock('@/components/PlayCodeMultiSelect', () => ({
  default: ({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) => (
    <button type="button" onClick={() => onChange(['DX1'])}>
      play:{value.join(',') || 'empty'}
    </button>
  ),
}));

vi.mock('@/components/PlayCodeSelect', () => ({
  default: ({ value, onChange }: { value: string; onChange: (next: string) => void }) => (
    <select
      aria-label="LuckySB play code"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">-- Select --</option>
      <option value="LUCKYSB_B1_01">Champion 01</option>
      <option value="LUCKYSB_B2_10">Runner-up 10</option>
    </select>
  ),
}));

import { listAccounts } from '@/api/accounts';
import { createStrategy } from '@/api/strategies';

const mockListAccounts = vi.mocked(listAccounts);
const mockCreateStrategy = vi.mocked(createStrategy);

const accounts = [
  {
    id: 1,
    account_name: 'jnd',
    password_masked: 'jn****',
    game_type: 'JND28',
    latest_verification_run_id: 101,
    effective_verification_run_id: 101,
    verification_stale: false,
    summary_status_reason: null,
    allowed_strategy_platform_types: ['JND28WEB', 'JND282'],
    platform_capabilities: [
      { platform_type: 'JND28WEB', verify_status: 'supported', market_state: 'open' },
      { platform_type: 'JND282', verify_status: 'supported', market_state: 'open' },
    ],
    platform_type: 'JND28WEB',
    status: 'inactive',
    balance: 0,
    kill_switch: false,
    last_login_at: null,
  },
  {
    id: 2,
    account_name: 'lucky',
    password_masked: 'lu****',
    game_type: 'LUCKYSB',
    latest_verification_run_id: 202,
    effective_verification_run_id: 202,
    verification_stale: false,
    summary_status_reason: null,
    allowed_strategy_platform_types: ['LUCKYSB'],
    platform_capabilities: [
      { platform_type: 'LUCKYSB', verify_status: 'supported', market_state: 'open' },
    ],
    platform_type: 'LUCKYSB',
    platform_url: 'https://member.example',
    status: 'inactive',
    balance: 0,
    kill_switch: false,
    last_login_at: null,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  mockListAccounts.mockResolvedValue({ code: 0, message: 'success', data: accounts });
  mockCreateStrategy.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      id: 1,
      account_id: 2,
      name: 'lucky flat',
      type: 'flat',
      play_code: 'LUCKYSB_B1_01',
      base_amount: 5,
      martin_sequence: null,
      bet_timing: 30,
      simulation: false,
      status: 'stopped',
      martin_level: 0,
      stop_loss: null,
      take_profit: null,
      daily_pnl: 0,
      total_pnl: 0,
      platform_type: 'LUCKYSB',
    },
  });
});

describe('StrategyForm', () => {
  it('preselects account and platform from create intent', async () => {
    render(
      <StrategyForm
        strategy={null}
        initialAccountId={2}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('2');
    });
    expect((document.getElementById('sf-platform') as HTMLSelectElement).value).toBe('LUCKYSB');
    expect(document.getElementById('sf-platform')).toBeDisabled();
  });

  it('defaults base amount to 1 yuan', async () => {
    render(
      <StrategyForm
        strategy={null}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('1');
    });
    expect((document.getElementById('sf-amount') as HTMLInputElement).value).toBe('1');
  });

  it('clears invalid strategy type when switching to lucky account', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <StrategyForm
        strategy={null}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('1');
    });

    const typeToggles = container.querySelectorAll('.type-toggle');
    const strategyTypeButtons = typeToggles[1]?.querySelectorAll('button') ?? [];
    await user.click(strategyTypeButtons[2] as HTMLButtonElement);
    expect(strategyTypeButtons[2]).toHaveClass('type-toggle-active');

    await user.selectOptions(document.getElementById('sf-account') as HTMLSelectElement, '2');
    await waitFor(() => {
      const refreshedButtons = container.querySelectorAll('.type-toggle')[0]?.querySelectorAll('button') ?? [];
      expect(refreshedButtons.length).toBe(2);
    });

    await user.click(container.querySelector('.form-submit-btn') as HTMLButtonElement);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('submits lucky strategy with single-play code and real platform value', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    const { container } = render(
      <StrategyForm
        strategy={null}
        initialAccountId={2}
        onDone={onDone}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('2');
    });

    await user.type(document.getElementById('sf-name') as HTMLInputElement, 'lucky flat');
    await user.selectOptions(screen.getByLabelText('LuckySB play code'), 'LUCKYSB_B1_01');
    await user.clear(document.getElementById('sf-amount') as HTMLInputElement);
    await user.type(document.getElementById('sf-amount') as HTMLInputElement, '5');
    await user.click(container.querySelector('.form-submit-btn') as HTMLButtonElement);

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalledWith(
        expect.objectContaining({
          account_id: 2,
          type: 'flat',
          play_code: 'LUCKYSB_B1_01',
          platform_type: 'LUCKYSB',
        }),
      );
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('lets JND account choose JND282 and submits backend platform value', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    const { container } = render(
      <StrategyForm
        strategy={null}
        initialAccountId={1}
        onDone={onDone}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('1');
    });

    await user.type(document.getElementById('sf-name') as HTMLInputElement, 'jnd flat');
    await user.click(screen.getByRole('button', { name: 'play:empty' }));
    await user.clear(document.getElementById('sf-amount') as HTMLInputElement);
    await user.type(document.getElementById('sf-amount') as HTMLInputElement, '5');
    await user.selectOptions(document.getElementById('sf-platform') as HTMLSelectElement, 'JND282');
    await user.click(container.querySelector('.form-submit-btn') as HTMLButtonElement);

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalledWith(
        expect.objectContaining({
          account_id: 1,
          play_code: 'DX1',
          platform_type: 'JND282',
        }),
      );
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('shows platform empty state and disables submit when verification is stale', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 3,
          account_name: 'stale_jnd',
          password_masked: 'st****',
          game_type: 'JND28',
          latest_verification_run_id: 303,
          effective_verification_run_id: 303,
          verification_stale: true,
          summary_status_reason: 'not_verified',
          allowed_strategy_platform_types: [],
          platform_capabilities: [],
          platform_type: 'JND28WEB',
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    const { container } = render(
      <StrategyForm
        strategy={null}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('3');
    });
    expect(screen.getByText(/verification is stale/i)).toBeInTheDocument();

    const platformSelect = document.getElementById('sf-platform') as HTMLSelectElement;
    expect(platformSelect).toBeDisabled();
    expect(platformSelect.value).toBe('');
    expect(screen.getByRole('option', { name: 'No verified platform available' })).toBeInTheDocument();

    const submitButton = container.querySelector('.form-submit-btn') as HTMLButtonElement;
    expect(submitButton).toBeDisabled();
    expect(mockCreateStrategy).not.toHaveBeenCalled();
  });

  it('shows the DW3 multi-strategy risk warning for the same account', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <StrategyForm
        strategy={null}
        existingStrategies={[
          {
            id: 99,
            account_id: 1,
            name: 'existing dw3',
            type: 'flat',
            play_code: 'DW3_BS_BBB,DW3_OE_OOO',
            base_amount: 10,
            martin_sequence: null,
            bet_timing: 30,
            simulation: false,
            status: 'running',
            martin_level: 0,
            stop_loss: null,
            take_profit: null,
            daily_pnl: 0,
            total_pnl: 0,
            platform_type: 'JND28WEB',
            gate_window_issues: 1,
          },
        ]}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('1');
    });

    const modeButtons = container.querySelectorAll('.type-toggle')[0]?.querySelectorAll('button') ?? [];
    await user.click(modeButtons[1] as HTMLButtonElement);
    expect(container.querySelector('.form-warning')).toBeInTheDocument();
  });
});
