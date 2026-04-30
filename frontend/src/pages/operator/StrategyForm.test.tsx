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
import { createStrategy, updateStrategy } from '@/api/strategies';
import type { AccountInfo } from '@/types/api/account';
import type { StrategyPermissionType } from '@/types/api/strategy';

const mockListAccounts = vi.mocked(listAccounts);
const mockCreateStrategy = vi.mocked(createStrategy);
const mockUpdateStrategy = vi.mocked(updateStrategy);

const allStrategyPermissions: StrategyPermissionType[] = [
  'flat',
  'martin',
  'dw3_flat',
  'dw3_martin',
  'red_wave_double_martin',
  'green_wave_single_martin',
  'omission_random_flat',
  'omission_random_martin',
  'ai_random_flat',
  'ai_random_martin',
];

const accounts: AccountInfo[] = [
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
    allowed_strategy_types: [...allStrategyPermissions],
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
    allowed_strategy_types: ['flat', 'martin'],
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
  mockUpdateStrategy.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      id: 9,
      account_id: 1,
      name: 'edit strategy',
      type: 'flat',
      play_code: 'DX1',
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
      platform_type: 'JND28WEB',
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
    expect((document.getElementById('sf-timing') as HTMLInputElement).value).toBe('88');
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

  it('shows empty strategy choices when account has no strategy permission', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          ...accounts[0],
          id: 7,
          account_name: 'no_permission',
          allowed_strategy_types: [],
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
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('7');
    });
    expect(screen.getByText('当前账号暂未开通策略，请联系管理员')).toBeInTheDocument();
    expect(container.querySelector('.form-submit-btn')).toBeDisabled();
  });

  it('removes the simulation switch during create flow but keeps it for edit flow', async () => {
    const { rerender } = render(
      <StrategyForm
        strategy={null}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('1');
    });
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();

    rerender(
      <StrategyForm
        strategy={{
          id: 9,
          account_id: 1,
          name: 'edit strategy',
          type: 'flat',
          play_code: 'DX1',
          play_code_name: '大小单双',
          base_amount: 5,
          martin_sequence: null,
          bet_timing: 30,
          simulation: true,
          status: 'stopped',
          martin_level: 0,
          stop_loss: null,
          take_profit: null,
          daily_pnl: 0,
          total_pnl: 0,
          platform_type: 'JND28WEB',
        }}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('switch')).toBeInTheDocument();
    });
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
    expect(screen.getByRole('option', { name: 'WEB' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '2.0' })).toBeInTheDocument();

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

  it('requires confirmation when create bet timing is below 20 seconds', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { container } = render(
      <StrategyForm
        strategy={null}
        initialAccountId={1}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    try {
      await waitFor(() => {
        expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('1');
      });

      await user.type(document.getElementById('sf-name') as HTMLInputElement, 'low timing');
      await user.click(screen.getByRole('button', { name: 'play:empty' }));
      await user.clear(document.getElementById('sf-timing') as HTMLInputElement);
      await user.type(document.getElementById('sf-timing') as HTMLInputElement, '15');
      await user.click(container.querySelector('.form-submit-btn') as HTMLButtonElement);

      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('低于20秒'));
      expect(mockCreateStrategy).not.toHaveBeenCalled();
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it('submits green-wave single strategy with default DS3 direction', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();

    render(
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

    await user.type(document.getElementById('sf-name') as HTMLInputElement, 'green single');
    await user.click(screen.getByRole('button', { name: '绿波追单' }));
    await user.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalledWith(
        expect.objectContaining({
          account_id: 1,
          type: 'green_wave_single_martin',
          play_code: 'DS3',
          platform_type: 'JND28WEB',
        }),
      );
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('submits omission-random martin strategy config', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();

    render(
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

    await user.type(document.getElementById('sf-name') as HTMLInputElement, 'random martin');
    await user.click(screen.getByRole('button', { name: '遗漏随机马丁' }));
    await user.click(screen.getByLabelText('球2'));
    await user.click(screen.getByRole('button', { name: '5个' }));
    await user.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalledWith(
        expect.objectContaining({
          account_id: 1,
          type: 'omission_random_martin',
          play_code: 'OMR_BALL1,OMR_BALL2',
          martin_sequence: [1, 2, 4, 8, 16],
          strategy_config: {
            pick_count: 5,
            categories: ['ball1', 'ball2'],
            weight_mode: 'omission_plus_random',
          },
          platform_type: 'JND28WEB',
        }),
      );
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('submits ai-random martin strategy config', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();

    render(
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

    await user.type(document.getElementById('sf-name') as HTMLInputElement, 'ai martin');
    await user.click(screen.getByRole('button', { name: 'AI推荐马丁' }));
    await user.click(screen.getByLabelText('球2'));
    await user.click(screen.getByRole('button', { name: '6个' }));
    await user.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalledWith(
        expect.objectContaining({
          account_id: 1,
          type: 'ai_random_martin',
          play_code: 'OMR_BALL1,OMR_BALL2',
          martin_sequence: [1, 2, 4, 8, 16],
          strategy_config: {
            pick_count: 6,
            categories: ['ball1', 'ball2'],
            weight_mode: 'pure_random',
          },
          platform_type: 'JND28WEB',
        }),
      );
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('updates existing green-wave single strategy directions', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();

    render(
      <StrategyForm
        strategy={{
          id: 12,
          account_id: 1,
          name: 'green edit',
          type: 'green_wave_single_martin',
          play_code: 'DS3',
          play_code_name: '单',
          base_amount: 5,
          martin_sequence: [1, 2, 4],
          bet_timing: 30,
          simulation: false,
          status: 'stopped',
          martin_level: 0,
          stop_loss: null,
          take_profit: null,
          daily_pnl: 0,
          total_pnl: 0,
          platform_type: 'JND28WEB',
        }}
        onDone={onDone}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '更新' })).toBeInTheDocument();
    });

    const directionCheckboxes = Array.from(document.querySelectorAll('.direction-checkbox'));
    expect(directionCheckboxes).toHaveLength(4);

    await user.click(directionCheckboxes[0] as HTMLInputElement);
    await user.click(screen.getByRole('button', { name: '更新' }));

    await waitFor(() => {
      expect(mockUpdateStrategy).toHaveBeenCalledWith(
        12,
        expect.objectContaining({
          play_code: 'B1LM_D,DS3',
          platform_type: 'JND28WEB',
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
    expect(screen.getByText('当前账号验证结果已失效，请重新验证后再创建策略。')).toBeInTheDocument();

    const platformSelect = document.getElementById('sf-platform') as HTMLSelectElement;
    expect(platformSelect).toBeDisabled();
    expect(platformSelect.value).toBe('');
    expect(screen.getByRole('option', { name: '暂无可用盘口' })).toBeInTheDocument();

    const submitButton = container.querySelector('.form-submit-btn') as HTMLButtonElement;
    expect(submitButton).toBeDisabled();
    expect(mockCreateStrategy).not.toHaveBeenCalled();
  });

  it('blocks JND submit when no effective verified platform is available', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 4,
          account_name: 'jnd_no_verified',
          password_masked: 'jn****',
          game_type: 'JND28',
          latest_verification_run_id: 404,
          effective_verification_run_id: null,
          verification_stale: false,
          summary_status_reason: 'not_verified',
          allowed_strategy_platform_types: [],
          platform_capabilities: [],
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
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('4');
    });
    expect(screen.getByText('当前账号暂无有效验证结果，请先完成账号验证。')).toBeInTheDocument();

    const submitButton = container.querySelector('.form-submit-btn') as HTMLButtonElement;
    expect(submitButton).toBeDisabled();
    expect(mockCreateStrategy).not.toHaveBeenCalled();
  });

  it('locks JND platform selector when only one verified platform exists', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 6,
          account_name: 'jnd_only_20',
          password_masked: 'jn****',
          game_type: 'JND28',
          latest_verification_run_id: 606,
          effective_verification_run_id: 606,
          verification_stale: false,
          summary_status_reason: null,
          allowed_strategy_platform_types: ['JND282'],
          allowed_strategy_types: allStrategyPermissions,
          platform_capabilities: [
            { platform_type: 'JND282', verify_status: 'supported', market_state: 'open' },
          ],
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(
      <StrategyForm
        strategy={null}
        onDone={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((document.getElementById('sf-account') as HTMLSelectElement).value).toBe('6');
    });

    const platformSelect = document.getElementById('sf-platform') as HTMLSelectElement;
    expect(platformSelect.value).toBe('JND282');
    expect(platformSelect).toBeDisabled();
    expect(screen.getByRole('option', { name: '2.0' })).toBeInTheDocument();
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
