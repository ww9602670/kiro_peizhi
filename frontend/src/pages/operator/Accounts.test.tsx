import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Accounts from './Accounts';

vi.mock('@/api/accounts', () => ({
  listAccounts: vi.fn(),
  createAccount: vi.fn(),
  deleteAccount: vi.fn(),
  verifyAccount: vi.fn(),
  logoutAccount: vi.fn(),
  updateKillSwitch: vi.fn(),
}));

vi.mock('@/api/odds', () => ({
  getAccountOdds: vi.fn(),
  confirmAccountOdds: vi.fn(),
  refreshAccountOdds: vi.fn(),
}));

vi.mock('@/api/request', () => ({
  isApiError: () => false,
}));

import { createAccount, listAccounts, verifyAccount } from '@/api/accounts';
import { getAccountOdds } from '@/api/odds';

const mockListAccounts = vi.mocked(listAccounts);
const mockCreateAccount = vi.mocked(createAccount);
const mockVerifyAccount = vi.mocked(verifyAccount);
const mockGetAccountOdds = vi.mocked(getAccountOdds);

beforeEach(() => {
  vi.clearAllMocks();
  mockListAccounts.mockResolvedValue({ code: 0, message: 'success', data: [] });
  mockCreateAccount.mockResolvedValue({ code: 0, message: 'success', data: null });
  mockVerifyAccount.mockResolvedValue({ code: 0, message: 'success', data: null });
  mockGetAccountOdds.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      account_id: 1,
      platform_type: 'JND28WEB',
      items: [],
      has_unconfirmed: false,
    },
  });
});

describe('Accounts', () => {
  it('renders localized account-management copy', async () => {
    const user = userEvent.setup();
    const { container } = render(<Accounts />);

    expect(await screen.findByRole('heading', { name: '账号管理' })).toBeInTheDocument();

    await user.click(container.querySelector('.bind-toggle-btn') as HTMLButtonElement);

    expect(screen.getByRole('heading', { name: '绑定账号' })).toBeInTheDocument();
    expect(document.getElementById('bind-name')).toHaveAttribute('placeholder', '请输入账号');
  });

  it('submits JND account payload with game_type', async () => {
    const user = userEvent.setup();
    const { container } = render(<Accounts />);

    await user.click(container.querySelector('.bind-toggle-btn') as HTMLButtonElement);
    await user.type(document.getElementById('bind-name') as HTMLInputElement, 'jnd001');
    await user.type(document.getElementById('bind-password') as HTMLInputElement, 'secret');
    await user.type(document.getElementById('bind-platform-url') as HTMLInputElement, 'https://merchant.example');
    await user.click(container.querySelector('.bind-submit-btn') as HTMLButtonElement);

    await waitFor(() => {
      expect(mockCreateAccount).toHaveBeenCalledWith({
        account_name: 'jnd001',
        password: 'secret',
        game_type: 'JND28',
        platform_url: 'https://merchant.example',
      });
    });
  });

  it('keeps URL field visible when switching to LUCKYSB', async () => {
    const user = userEvent.setup();
    const { container } = render(<Accounts />);

    await user.click(container.querySelector('.bind-toggle-btn') as HTMLButtonElement);
    await user.type(document.getElementById('bind-platform-url') as HTMLInputElement, 'https://member.example');
    await user.selectOptions(document.getElementById('bind-game-type') as HTMLSelectElement, 'LUCKYSB');

    const memberSiteUrl = document.getElementById('bind-platform-url') as HTMLInputElement;
    expect(memberSiteUrl).toHaveValue('https://member.example');
  });

  it('renders game-type options in the account form', async () => {
    const user = userEvent.setup();
    const { container } = render(<Accounts />);

    await user.click(container.querySelector('.bind-toggle-btn') as HTMLButtonElement);

    const gameTypeSelect = document.getElementById('bind-game-type') as HTMLSelectElement;
    const optionValues = Array.from(gameTypeSelect.options).map((option) => option.value);
    expect(optionValues).toEqual(['JND28', 'LUCKYSB']);
  });

  it('calls verifyAccount when clicking verify button', async () => {
    const user = userEvent.setup();
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 1,
          account_name: 'acc-1',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          summary_status_reason: null,
          verification_stale: false,
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    await user.click(await screen.findByRole('button', { name: '验证账号' }));

    await waitFor(() => {
      expect(mockVerifyAccount).toHaveBeenCalledWith(1);
    });
  });

  it.skip('merges rapid verify clicks into one request', async () => {
    const user = userEvent.setup();
    let releaseVerify: (() => void) | undefined;
    mockVerifyAccount.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseVerify = () => resolve({ code: 0, message: 'success', data: null as never });
        }) as never
    );
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 11,
          account_name: 'acc-11',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          summary_status_reason: 'not_verified',
          verification_stale: false,
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    const verifyButton = await screen.findByRole('button', { name: '楠岃瘉璐﹀彿' });
    await Promise.all([user.click(verifyButton), user.click(verifyButton)]);

    expect(mockVerifyAccount).toHaveBeenCalledTimes(1);
    releaseVerify?.();
  });

  it('coalesces repeated verify actions', async () => {
    const user = userEvent.setup();
    let releaseVerify: (() => void) | undefined;
    mockVerifyAccount.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseVerify = () => resolve({ code: 0, message: 'success', data: null as never });
        }) as never
    );
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 12,
          account_name: 'acc-12',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          summary_status_reason: 'not_verified',
          verification_stale: false,
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    const { container } = render(<Accounts />);
    const verifyButton = await waitFor(() => {
      const button = container.querySelector('.action-btn-login') as HTMLButtonElement | null;
      if (!button) throw new Error('verify button not ready');
      return button;
    });

    await Promise.all([user.click(verifyButton), user.click(verifyButton)]);
    expect(mockVerifyAccount).toHaveBeenCalledTimes(1);
    releaseVerify?.();
  });

  it('uses frontend signal to show relogin guidance and disable create strategy', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 13,
          account_name: 'acc-13',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: ['JND282'],
          frontend_signal: 'need_relogin',
          frontend_signal_reason: 'session_login_error',
          summary_status_reason: null,
          verification_stale: false,
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts onCreateStrategy={vi.fn()} />);

    expect(await screen.findByText('需要人工处理：请前往账号页重新登录后再试。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新登录账号' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去创建策略' })).toBeDisabled();
  });

  it.skip('renders summary status and summary reason from verification result', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 2,
          account_name: 'acc-2',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          summary_status_reason: 'unsupported_only',
          verification_stale: false,
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    expect(await screen.findByText('验证失败')).toBeInTheDocument();
    expect(screen.getByText('平台不支持')).toBeInTheDocument();
  });

  it.skip('renders operator-facing summary reason from verification result', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 2,
          account_name: 'acc-2',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          summary_status_reason: 'unsupported_only',
          verification_stale: false,
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    expect(await screen.findByText('需要人工处理：当前账号暂不支持自动操作，请更换账号。')).toBeInTheDocument();
  });

  it.skip('renders structured odds message on the account card', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 21,
          account_name: 'acc-odds',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          summary_status_reason: 'not_verified',
          verification_stale: false,
          odds_synced: false,
          odds_message: 'account bound, please verify account',
          status: 'inactive',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    expect(await screen.findByText('需要人工处理：请前往账号页重新登录后再试。')).toBeInTheDocument();
    expect(screen.queryByText('account bound, please verify account')).not.toBeInTheDocument();
  });

  it('uses verified platform capability as platform source (no game_type fallback)', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 3,
          account_name: 'acc-3',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          platform_capabilities: [
            { platform_type: 'LUCKYSB', verify_status: 'supported', market_state: 'open' },
          ],
          summary_status_reason: null,
          effective_verification_run_id: 3001,
          status: 'online',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });
    mockGetAccountOdds.mockResolvedValue({
      code: 0,
      message: 'success',
      data: {
        account_id: 3,
        platform_type: 'LUCKYSB',
        items: [],
        has_unconfirmed: false,
      },
    });

    render(<Accounts />);

    await waitFor(() => {
      expect(mockGetAccountOdds).toHaveBeenCalledWith(3, 'LUCKYSB');
    });
  });

  it('does not fallback to static game_type when no verified platform exists', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 4,
          account_name: 'acc-4',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: [],
          platform_capabilities: [
            { platform_type: 'JND28WEB', verify_status: 'unsupported', market_state: 'unknown' },
            { platform_type: 'JND282', verify_status: 'probe_failed', market_state: 'unknown' },
          ],
          summary_status_reason: 'unsupported_with_probe_failed',
          effective_verification_run_id: null,
          status: 'online',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    await waitFor(() => {
      expect(screen.getByText('验证失败')).toBeInTheDocument();
    });
    expect(mockGetAccountOdds).not.toHaveBeenCalled();
  });

  it('shows game type on account card without exposing JND platform split', async () => {
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 5,
          account_name: 'acc-5',
          password_masked: 'ac****',
          game_type: 'JND28',
          allowed_strategy_platform_types: ['JND28WEB', 'JND282'],
          platform_capabilities: [
            { platform_type: 'JND28WEB', verify_status: 'supported', market_state: 'open' },
            { platform_type: 'JND282', verify_status: 'supported', market_state: 'open' },
          ],
          summary_status_reason: null,
          effective_verification_run_id: 5001,
          status: 'online',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    expect((await screen.findAllByText('加拿大28')).length).toBeGreaterThan(0);
    expect(screen.queryByText('加拿大28网页版')).not.toBeInTheDocument();
    expect(screen.queryByText('加拿大282.0版')).not.toBeInTheDocument();
  });
});
