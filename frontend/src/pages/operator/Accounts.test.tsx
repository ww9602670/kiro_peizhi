import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Accounts from './Accounts';

vi.mock('@/api/accounts', () => ({
  listAccounts: vi.fn(),
  createAccount: vi.fn(),
  deleteAccount: vi.fn(),
  loginAccount: vi.fn(),
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

import { createAccount, listAccounts } from '@/api/accounts';
import { getAccountOdds } from '@/api/odds';

const mockListAccounts = vi.mocked(listAccounts);
const mockCreateAccount = vi.mocked(createAccount);
const mockGetAccountOdds = vi.mocked(getAccountOdds);

beforeEach(() => {
  vi.clearAllMocks();
  mockListAccounts.mockResolvedValue({ code: 0, message: 'success', data: [] });
  mockCreateAccount.mockResolvedValue({
    code: 0,
    message: 'success',
    data: {
      id: 1,
      account_name: 'player001',
      password_masked: 'pl****',
      game_type: 'LUCKYSB',
      allowed_strategy_platform_types: ['LUCKYSB'],
      platform_type: 'LUCKYSB',
      platform_url: 'https://member.example',
      status: 'inactive',
      balance: 0,
      kill_switch: false,
      last_login_at: null,
    },
  });
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

    const platformUrl = document.getElementById('bind-platform-url') as HTMLInputElement;
    expect(platformUrl).toBeRequired();
    await user.type(platformUrl, 'https://merchant.example');
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
    expect(memberSiteUrl).toBeInTheDocument();
    expect(memberSiteUrl).toHaveValue('https://member.example');

    await user.type(document.getElementById('bind-name') as HTMLInputElement, 'lucky001');
    await user.type(document.getElementById('bind-password') as HTMLInputElement, 'secret');
    await user.click(container.querySelector('.bind-submit-btn') as HTMLButtonElement);

    await waitFor(() => {
      expect(mockCreateAccount).toHaveBeenCalledWith({
        account_name: 'lucky001',
        password: 'secret',
        game_type: 'LUCKYSB',
        platform_url: 'https://member.example',
      });
    });
  });

  it('renders game-type options in the account form', async () => {
    const user = userEvent.setup();
    const { container } = render(<Accounts />);

    await user.click(container.querySelector('.bind-toggle-btn') as HTMLButtonElement);

    const gameTypeSelect = document.getElementById('bind-game-type') as HTMLSelectElement;
    const optionValues = Array.from(gameTypeSelect.options).map((option) => option.value);
    expect(optionValues).toEqual(['JND28', 'LUCKYSB']);
  });

  it('requests odds with an explicit platform_type when switching JND platforms', async () => {
    const user = userEvent.setup();
    mockListAccounts.mockResolvedValue({
      code: 0,
      message: 'success',
      data: [
        {
          id: 1,
          account_name: 'jnd001',
          password_masked: 'jn****',
          game_type: 'JND28',
          allowed_strategy_platform_types: ['JND28WEB', 'JND282'],
          status: 'online',
          balance: 0,
          kill_switch: false,
          last_login_at: null,
        },
      ],
    });

    render(<Accounts />);

    await waitFor(() => {
      expect(mockGetAccountOdds).toHaveBeenCalledWith(1, 'JND28WEB');
    });

    await user.click(await screen.findByRole('button', { name: /赔率详情/i }));
    await user.selectOptions(screen.getByRole('combobox'), 'JND282');

    await waitFor(() => {
      expect(mockGetAccountOdds).toHaveBeenLastCalledWith(1, 'JND282');
    });
  });
});
