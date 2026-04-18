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

    await user.click(await screen.findByRole('button', { name: '验证账号' }));

    await waitFor(() => {
      expect(mockVerifyAccount).toHaveBeenCalledWith(1);
    });
  });

  it('renders summary status and summary reason from verification result', async () => {
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
});
