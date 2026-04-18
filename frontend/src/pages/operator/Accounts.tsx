/**
 * 博彩账号管理页面（Mobile-First）
 * - 账号列表（卡片布局）
 * - 绑定表单（可展开）
 * - 密码脱敏显示
 * - 熔断开关
 * - 赔率详情面板（可展开，显示赔率明细）
 */

import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { isApiError } from '@/api/request';
import {
  listAccounts,
  createAccount,
  deleteAccount,
  verifyAccount,
  logoutAccount,
  updateKillSwitch,
} from '@/api/accounts';
import { getAccountOdds, confirmAccountOdds, refreshAccountOdds } from '@/api/odds';
import type { AccountCreate, AccountGameType, AccountInfo } from '@/types/api/account';
import type { OddsItem, OddsRefreshResponse, PeriodInfo } from '@/types/api/odds';
import ConfirmDialog from '@/components/ConfirmDialog';
import Toast from '@/components/Toast';
import { useConfirm } from '@/hooks/useConfirm';
import { useToast } from '@/hooks/useToast';
import { getPlatformLabel } from '@/utils/platformLabels';
import './Accounts.css';

function getStatusLabel(status: string): string {
  switch (status) {
    case 'online':
      return '在线';
    case 'inactive':
      return '未验证';
    case 'login_error':
      return '验证异常';
    case 'disabled':
      return '已禁用';
    default:
      return status;
  }
}

const ACCOUNT_GAME_OPTIONS: { value: AccountGameType; label: string }[] = [
  { value: 'JND28', label: '加拿大28' },
  { value: 'LUCKYSB', label: getPlatformLabel('LUCKYSB') },
];

function getGameTypeLabel(gameType: string): string {
  if (gameType === 'JND28') return '加拿大28';
  if (gameType === 'LUCKYSB') return getPlatformLabel('LUCKYSB');
  return gameType;
}

type AccountSummaryState =
  | 'not_verified'
  | 'verified'
  | 'partially_available'
  | 'failed'
  | 'stale'
  | 'verifying';

function normalizePlatformType(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().toUpperCase();
  if (!normalized) return null;
  return normalized;
}

function resolveAccountGameTypeNoFallback(account: Pick<AccountInfo, 'game_type'>): string {
  if (!account.game_type) return '';
  return `${account.game_type}`.toUpperCase();
}

function resolveVerifiedPlatformOptions(
  account: Pick<AccountInfo, 'allowed_strategy_platform_types' | 'platform_capabilities'>
): string[] {
  const fromAllowed = Array.from(
    new Set((account.allowed_strategy_platform_types ?? []).map(normalizePlatformType).filter(Boolean))
  ) as string[];
  if (fromAllowed.length > 0) return fromAllowed;

  return Array.from(
    new Set(
      (account.platform_capabilities ?? [])
        .filter((item) => `${item.verify_status}`.toLowerCase() === 'supported')
        .map((item) => normalizePlatformType(item.platform_type))
        .filter(Boolean)
    )
  ) as string[];
}

function resolveSummaryState(account: AccountInfo): AccountSummaryState {
  if (account.verification_in_progress) return 'verifying';
  if (account.verification_stale) return 'stale';

  switch (account.summary_status_reason) {
    case 'not_verified':
      return 'not_verified';
    case 'probe_partial_failure':
      return 'partially_available';
    case 'unsupported_only':
    case 'probe_failed_only':
    case 'unsupported_with_probe_failed':
      return 'failed';
    default:
      break;
  }

  if (typeof account.effective_verification_run_id === 'number' && account.effective_verification_run_id > 0) {
    return 'verified';
  }

  return 'not_verified';
}

function getSummaryBadgeClass(summaryState: AccountSummaryState): string {
  switch (summaryState) {
    case 'verified':
      return 'badge-status-online';
    case 'partially_available':
      return 'badge-status-inactive';
    case 'failed':
    case 'stale':
      return 'badge-status-error';
    case 'verifying':
      return 'badge-status-disabled';
    default:
      return 'badge-status-inactive';
  }
}

function getSummaryLabel(summaryState: AccountSummaryState): string {
  switch (summaryState) {
    case 'verified':
      return '已验证';
    case 'partially_available':
      return '部分可用';
    case 'failed':
      return '验证失败';
    case 'stale':
      return '需重验';
    case 'verifying':
      return '验证中';
    default:
      return '未验证';
  }
}

function getSummaryStatusReasonLabel(
  reason: AccountInfo['summary_status_reason'],
  verificationStale: boolean | undefined
): string | null {
  if (verificationStale) return '验证结果已失效，请重新验证';

  switch (reason) {
    case 'not_verified':
      return '尚未完成验证';
    case 'probe_partial_failure':
      return '部分平台探测失败';
    case 'unsupported_only':
      return '平台不支持';
    case 'probe_failed_only':
      return '平台探测异常';
    case 'unsupported_with_probe_failed':
      return '平台不支持且存在探测异常';
    default:
      return null;
  }
}

interface AccountsProps {
  onCreateStrategy?: (accountId: number) => void;
}

export default function Accounts({ onCreateStrategy }: AccountsProps) {
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);

  // Bind form state
  const [formName, setFormName] = useState('');
  const [formPassword, setFormPassword] = useState('');
  const [formGameType, setFormGameType] = useState<AccountGameType>('JND28');
  const [formPlatformUrl, setFormPlatformUrl] = useState('');
  const [formError, setFormError] = useState('');
  const [formLoading, setFormLoading] = useState(false);

  // Per-account action loading
  const [actionLoading, setActionLoading] = useState<Record<number, string>>({});

  // Dialog & Toast hooks
  const { confirmState, confirm, handleConfirm, handleCancel } = useConfirm();
  const { messages, showToast, removeToast } = useToast();

  const fetchAccounts = useCallback(async () => {
    try {
      setError('');
      const res = await listAccounts();
      setAccounts(res.data ?? []);
    } catch (err) {
      if (isApiError(err)) {
        setError(err.message);
      } else {
        setError('加载账号列表失败');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  const handleBind = async (e: FormEvent) => {
    e.preventDefault();
    setFormError('');

    if (!formName.trim() || !formPassword.trim()) {
      setFormError('请填写账号和密码');
      return;
    }

    if (!formPlatformUrl.trim()) {
      setFormError('平台地址不能为空');
      return;
    }

    setFormLoading(true);
    try {
      const data: AccountCreate = {
        account_name: formName.trim(),
        password: formPassword,
        game_type: formGameType,
        platform_url: formPlatformUrl.trim(),
      };
      await createAccount(data);
      // Reset form & refresh list
      setFormName('');
      setFormPassword('');
      setFormGameType('JND28');
      setFormPlatformUrl('');
      setShowForm(false);
      await fetchAccounts();
    } catch (err) {
      if (isApiError(err)) {
        setFormError(err.message);
      } else {
        setFormError('绑定失败，请稍后重试');
      }
    } finally {
      setFormLoading(false);
    }
  };

  const handleVerify = async (id: number) => {
    setActionLoading((prev) => ({ ...prev, [id]: 'verify' }));
    try {
      await verifyAccount(id);
      await fetchAccounts();
    } catch (err) {
      showToast(isApiError(err) ? err.message : '验证失败');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const handleLogout = async (id: number) => {
    setActionLoading((prev) => ({ ...prev, [id]: 'logout' }));
    try {
      await logoutAccount(id);
      await fetchAccounts();
      showToast('已退出登录');
    } catch (err) {
      showToast(isApiError(err) ? err.message : '退出失败');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!(await confirm(`确定解绑账号「${name}」？`))) return;
    setActionLoading((prev) => ({ ...prev, [id]: 'delete' }));
    try {
      await deleteAccount(id);
      await fetchAccounts();
    } catch (err) {
      showToast(isApiError(err) ? err.message : '解绑失败');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const handleKillSwitch = async (id: number, currentEnabled: boolean) => {
    setActionLoading((prev) => ({ ...prev, [id]: 'kill' }));
    try {
      await updateKillSwitch(id, { enabled: !currentEnabled });
      await fetchAccounts();
    } catch (err) {
      showToast(isApiError(err) ? err.message : '操作失败');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  return (
    <div className="accounts-page">
      <Toast messages={messages} onRemove={removeToast} />
      <ConfirmDialog
        open={confirmState.open}
        message={confirmState.message}
        title={confirmState.title}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
      <div className="accounts-header">
        <h1 className="accounts-title">博彩账号</h1>
      </div>

      {/* Bind Form */}
      <div className="bind-section">
        {!showForm ? (
          <button
            className="bind-toggle-btn"
            onClick={() => setShowForm(true)}
            type="button"
          >
            + 绑定新账号
          </button>
        ) : (
          <form className="bind-form" onSubmit={handleBind}>
            <h2 className="bind-form-title">绑定博彩账号</h2>

            {formError && (
              <div role="alert" className="bind-error">
                {formError}
              </div>
            )}

            <div className="bind-field">
              <label htmlFor="bind-name" className="bind-label">
                账号名
              </label>
              <input
                id="bind-name"
                type="text"
                className="bind-input"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="请输入博彩账号"
                disabled={formLoading}
                autoComplete="off"
              />
            </div>

            <div className="bind-field">
              <label htmlFor="bind-password" className="bind-label">
                密码
              </label>
              <input
                id="bind-password"
                type="password"
                className="bind-input"
                value={formPassword}
                onChange={(e) => setFormPassword(e.target.value)}
                placeholder="请输入密码"
                disabled={formLoading}
                autoComplete="new-password"
              />
            </div>

            <div className="bind-field">
              <label htmlFor="bind-game-type" className="bind-label">
                游戏类型
              </label>
              <select
                id="bind-game-type"
                className="bind-input"
                value={formGameType}
                onChange={(e) => {
                  const nextGameType = e.target.value as AccountGameType;
                  setFormGameType(nextGameType);
                }}
                disabled={formLoading}
              >
                {ACCOUNT_GAME_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="bind-field">
              <label htmlFor="bind-platform-url" className="bind-label">
                {formGameType === 'LUCKYSB' ? '会员站地址' : '平台地址'}
              </label>
              <input
                id="bind-platform-url"
                type="text"
                className="bind-input"
                value={formPlatformUrl}
                onChange={(e) => setFormPlatformUrl(e.target.value)}
                placeholder={formGameType === 'LUCKYSB' ? '请输入会员站地址' : '请输入平台地址'}
                required
                disabled={formLoading}
              />
            </div>

            <div className="bind-actions">
              <button
                type="button"
                className="bind-cancel-btn"
                onClick={() => {
                  setShowForm(false);
                  setFormGameType('JND28');
                  setFormPlatformUrl('');
                  setFormError('');
                }}
                disabled={formLoading}
              >
                取消
              </button>
              <button
                type="submit"
                className="bind-submit-btn"
                disabled={formLoading}
              >
                {formLoading ? '绑定中...' : '绑定'}
              </button>
            </div>
          </form>
        )}
      </div>

      {/* Account List */}
      {loading && <div className="accounts-loading">加载中...</div>}

      {!loading && error && (
        <div className="accounts-error" role="alert">
          {error}
        </div>
      )}

      {!loading && !error && accounts.length === 0 && (
        <div className="accounts-empty">暂无绑定账号，点击上方按钮绑定</div>
      )}

      {!loading && !error && accounts.length > 0 && (
        <div className="account-list">
          {accounts.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              actionLoading={actionLoading[account.id]}
              onVerify={handleVerify}
              onLogout={handleLogout}
              onDelete={handleDelete}
              onKillSwitch={handleKillSwitch}
              onCreateStrategy={onCreateStrategy}
              showToast={showToast}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* --- AccountCard sub-component --- */

type OddsStatus = 'confirmed' | 'unconfirmed' | 'none';

function getOddsBadgeClass(status: OddsStatus): string {
  switch (status) {
    case 'confirmed':
      return 'badge-odds-confirmed';
    case 'unconfirmed':
      return 'badge-odds-unconfirmed';
    default:
      return 'badge-odds-none';
  }
}

function getOddsLabel(status: OddsStatus): string {
  switch (status) {
    case 'confirmed':
      return '赔率已确认';
    case 'unconfirmed':
      return '赔率待确认';
    default:
      return '赔率未获取';
  }
}

/** 赔率分组显示 */
const ODDS_GROUPS: { label: string; prefix: string }[] = [
  { label: '大小', prefix: 'DX' },
  { label: '单双', prefix: 'DS' },
  { label: '组合', prefix: 'ZH' },
  { label: '色波', prefix: 'SB' },
  { label: '极值', prefix: 'JDX' },
  { label: '豹子', prefix: 'BZ' },
  { label: '和值', prefix: 'HZ' },
  { label: '特码波色', prefix: 'TMBS' },
];

interface AccountOddsSyncStatus {
  status: OddsStatus;
  label: string;
  message: string;
  oddsCount: number | null;
}

function resolveAccountOddsSyncStatus(account: AccountInfo): AccountOddsSyncStatus | null {
  const synced = typeof account.odds_synced === 'boolean' ? account.odds_synced : null;
  const oddsCount =
    typeof account.odds_count === 'number' && Number.isFinite(account.odds_count)
      ? Math.max(0, Math.trunc(account.odds_count))
      : null;
  const message = typeof account.odds_message === 'string' ? account.odds_message.trim() : '';

  if (synced === null && oddsCount === null && !message) {
    return null;
  }

  if (synced === true) {
    return {
      status: 'confirmed',
      label: '赔率已同步',
      message,
      oddsCount,
    };
  }

  return {
    status: oddsCount !== null && oddsCount > 0 ? 'unconfirmed' : 'none',
    label: '赔率未同步',
    message,
    oddsCount,
  };
}

function resolveOddsRefreshResult(data: OddsRefreshResponse | undefined): {
  period: PeriodInfo | null;
  oddsCount: number;
  synced: boolean;
  message: string;
} | null {
  if (!data) return null;

  const oddsCount =
    typeof data.odds_count === 'number' && Number.isFinite(data.odds_count)
      ? Math.max(0, Math.trunc(data.odds_count))
      : 0;
  const synced = typeof data.odds_synced === 'boolean' ? data.odds_synced : Boolean(data.synced);
  const message =
    (typeof data.odds_message === 'string' && data.odds_message.trim()) ||
    (typeof data.message === 'string' && data.message.trim()) ||
    '赔率刷新完成';

  return {
    period: data.period ?? null,
    oddsCount,
    synced,
    message,
  };
}

function groupOdds(items: OddsItem[]): { label: string; items: OddsItem[] }[] {
  const groups: { label: string; items: OddsItem[] }[] = [];
  const used = new Set<string>();

  for (const g of ODDS_GROUPS) {
    const matched = items.filter((i) => i.key_code.startsWith(g.prefix));
    if (matched.length > 0) {
      groups.push({ label: g.label, items: matched });
      for (const m of matched) used.add(m.key_code);
    }
  }

  // 其他未分组的
  const rest = items.filter((i) => !used.has(i.key_code));
  if (rest.length > 0) {
    groups.push({ label: '其他', items: rest });
  }

  return groups;
}

interface AccountCardProps {
  account: AccountInfo;
  actionLoading?: string;
  onVerify: (id: number) => void;
  onLogout: (id: number) => void;
  onDelete: (id: number, name: string) => void;
  onKillSwitch: (id: number, currentEnabled: boolean) => void;
  onCreateStrategy?: (id: number) => void;
  showToast: (text: string) => void;
}

function AccountCard({
  account,
  actionLoading,
  onVerify,
  onLogout,
  onDelete,
  onKillSwitch,
  onCreateStrategy,
  showToast,
}: AccountCardProps) {
  const isActioning = !!actionLoading;
  const [oddsStatus, setOddsStatus] = useState<OddsStatus>('none');
  const [oddsItems, setOddsItems] = useState<OddsItem[]>([]);
  const [oddsLoading, setOddsLoading] = useState(false);
  const [oddsExpanded, setOddsExpanded] = useState(false);
  const [refreshLoading, setRefreshLoading] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<{ text: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [periodInfo, setPeriodInfo] = useState<PeriodInfo | null>(null);
  const oddsPlatformOptions = resolveVerifiedPlatformOptions(account);
  const oddsPlatformKey = oddsPlatformOptions.join('|');
  const [selectedOddsPlatform, setSelectedOddsPlatform] = useState<string>(oddsPlatformOptions[0] ?? '');
  const structuredOddsSync = resolveAccountOddsSyncStatus(account);
  const selectedPlatformLabel = selectedOddsPlatform ? getPlatformLabel(selectedOddsPlatform) : '--';
  const useStructuredOddsStatus = oddsPlatformOptions.length === 1;
  const displayOddsStatus = (useStructuredOddsStatus ? structuredOddsSync?.status : null) ?? oddsStatus;
  const displayOddsLabel = (useStructuredOddsStatus ? structuredOddsSync?.label : null) ?? getOddsLabel(oddsStatus);
  const accountGameType = resolveAccountGameTypeNoFallback(account);
  const summaryState = resolveSummaryState(account);
  const summaryReason = getSummaryStatusReasonLabel(account.summary_status_reason, account.verification_stale);
  const hasEffectiveVerification =
    typeof account.effective_verification_run_id === 'number' && account.effective_verification_run_id > 0;
  const platformBadgeText =
    oddsPlatformOptions.length > 0 ? oddsPlatformOptions.map((item) => getPlatformLabel(item)).join(' / ') : '未验证平台';
  const canRunOddsPanel = account.status === 'online' && selectedOddsPlatform.length > 0;
  const verifyButtonLabel = hasEffectiveVerification ? '重新验证账号' : '验证账号';
  const isVerifying = account.verification_in_progress || actionLoading === 'verify';
  const canCreateStrategy = oddsPlatformOptions.length > 0;

  useEffect(() => {
    setSelectedOddsPlatform((current) =>
      oddsPlatformOptions.includes(current) ? current : (oddsPlatformOptions[0] ?? '')
    );
  }, [oddsPlatformKey]);

  const fetchOddsStatus = useCallback(async () => {
    if (account.status !== 'online' || !selectedOddsPlatform) {
      setOddsStatus('none');
      setOddsItems([]);
      return;
    }
    try {
      const res = await getAccountOdds(account.id, selectedOddsPlatform);
      const data = res.data;
      if (!data || data.items.length === 0) {
        setOddsStatus('none');
        setOddsItems([]);
      } else if (data.has_unconfirmed) {
        setOddsStatus('unconfirmed');
        setOddsItems(data.items);
      } else {
        setOddsStatus('confirmed');
        setOddsItems(data.items);
      }
    } catch {
      setOddsStatus('none');
      setOddsItems([]);
    }
  }, [account.id, account.status, selectedOddsPlatform]);

  useEffect(() => {
    setPeriodInfo(null);
    setRefreshMsg(null);
    fetchOddsStatus();
  }, [fetchOddsStatus]);

  const handleConfirmOdds = async () => {
    setOddsLoading(true);
    try {
      await confirmAccountOdds(account.id, selectedOddsPlatform);
      await fetchOddsStatus();
      setRefreshMsg({ text: `${selectedPlatformLabel}赔率已确认`, type: 'success' });
    } catch (err) {
      showToast(isApiError(err) ? err.message : '确认赔率失败');
    } finally {
      setOddsLoading(false);
    }
  };

  const handleRefreshOdds = async () => {
    setRefreshLoading(true);
    setRefreshMsg(null);
    try {
      const res = await refreshAccountOdds(account.id, selectedOddsPlatform);
      const data = res.data as OddsRefreshResponse | undefined;
      const refreshResult = resolveOddsRefreshResult(data);
      if (refreshResult) {
        if (refreshResult.period) setPeriodInfo(refreshResult.period);
        if (refreshResult.synced) {
          setRefreshMsg({ text: refreshResult.message, type: 'success' });
          await fetchOddsStatus();
        } else if (refreshResult.oddsCount > 0) {
          setRefreshMsg({ text: refreshResult.message, type: 'error' });
        } else {
          setRefreshMsg({ text: refreshResult.message, type: 'info' });
        }
      }
    } catch (err) {
      if (isApiError(err)) {
        setRefreshMsg({ text: err.message, type: 'error' });
      } else {
        setRefreshMsg({ text: '赔率刷新失败', type: 'error' });
      }
    } finally {
      setRefreshLoading(false);
    }
  };

  const oddsGroups = groupOdds(oddsItems);

  return (
    <div className="account-card">
      <div className="account-card-header">
        <h3 className="account-name">{account.account_name}</h3>
        <div className="account-badges">
          <span className="badge badge-platform">{platformBadgeText}</span>
          <span className={`badge ${getSummaryBadgeClass(summaryState)}`}>
            {getSummaryLabel(summaryState)}
          </span>
        </div>
      </div>

      <div className="account-info">
        <div className="account-info-item">
          <span className="account-info-label">余额</span>
          <span className="account-info-value">{account.balance.toFixed(2)} 元</span>
        </div>
        <div className="account-info-item">
          <span className="account-info-label">密码</span>
          <span className="account-info-value">{account.password_masked}</span>
        </div>
        <div className="account-info-item">
          <span className="account-info-label">游戏类型</span>
          <span className="account-info-value">{getGameTypeLabel(accountGameType)}</span>
        </div>
        {account.platform_url && (
          <div className="account-info-item">
            <span className="account-info-label">
              {accountGameType === 'LUCKYSB' ? '会员站地址' : '平台地址'}
            </span>
            <span className="account-info-value" style={{ fontSize: 11, wordBreak: 'break-all' }}>
              {account.platform_url}
            </span>
          </div>
        )}
        <div className="account-info-item">
          <span className="account-info-label">
            {oddsPlatformOptions.length > 1 ? `赔率状态（${selectedPlatformLabel}）` : '赔率状态'}
          </span>
          <span className={`badge ${getOddsBadgeClass(displayOddsStatus)}`}>
            {displayOddsLabel}
          </span>
        </div>
        {summaryReason && (
          <div className="account-info-item">
            <span className="account-info-label">验证说明</span>
            <span className="account-info-value">{summaryReason}</span>
          </div>
        )}
        {oddsItems.length > 0 && (
          <div className="account-info-item">
            <span className="account-info-label">赔率数量</span>
            <span className="account-info-value">{oddsItems.length} 项</span>
          </div>
        )}
      </div>

      {/* Odds Detail Panel */}
      {canRunOddsPanel && (
        <div className="odds-panel">
          <div
            className="odds-panel-header"
            onClick={() => setOddsExpanded(!oddsExpanded)}
            role="button"
            tabIndex={0}
            aria-expanded={oddsExpanded}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setOddsExpanded(!oddsExpanded);
              }
            }}
          >
            <span className="odds-panel-title">
              赔率详情 {oddsItems.length > 0 ? `(${oddsItems.length}项)` : ''}
            </span>
            <span className="odds-panel-toggle">{oddsExpanded ? '收起 ▲' : '展开 ▼'}</span>
          </div>

          {oddsExpanded && (
            <div className="odds-panel-body">
              <div className="odds-platform-row">
                <span className="odds-platform-label">赔率平台</span>
                {oddsPlatformOptions.length > 1 ? (
                  <select
                    className="odds-platform-select"
                    value={selectedOddsPlatform}
                    onChange={(e) => setSelectedOddsPlatform(e.target.value)}
                    disabled={refreshLoading || oddsLoading || isActioning}
                  >
                    {oddsPlatformOptions.map((platformType) => (
                      <option key={platformType} value={platformType}>
                        {getPlatformLabel(platformType)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="badge badge-platform">{selectedPlatformLabel}</span>
                )}
              </div>

              {/* Period Info */}
              {periodInfo && (
                <div className="odds-period-info">
                  <div className="odds-period-item">
                    <span className="odds-period-label">期号:</span>
                    <span className="odds-period-value">{periodInfo.issue}</span>
                  </div>
                  <div className="odds-period-item">
                    <span className="odds-period-label">状态:</span>
                    <span className="odds-period-value">{periodInfo.state_label}</span>
                  </div>
                  <div className="odds-period-item">
                    <span className="odds-period-label">封盘倒计时:</span>
                    <span className="odds-period-value">{periodInfo.close_countdown_sec}s</span>
                  </div>
                  {periodInfo.pre_result && (
                    <div className="odds-period-item">
                      <span className="odds-period-label">上期结果:</span>
                      <span className="odds-period-value">{periodInfo.pre_result}</span>
                    </div>
                  )}
                </div>
              )}

              {/* Odds Table */}
              {oddsGroups.length > 0 ? (
                oddsGroups.map((group) => (
                  <div key={group.label} style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 12, color: '#666', fontWeight: 500, marginBottom: 4 }}>
                      {group.label}
                    </div>
                    <table className="odds-table">
                      <thead>
                        <tr>
                          <th>玩法</th>
                          <th>赔率</th>
                          <th>状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.items.map((item) => (
                          <tr key={item.key_code}>
                            <td>{item.key_code}</td>
                            <td>{(item.odds_value / 10000).toFixed(4)}</td>
                            <td>
                              <span
                                className={`badge ${item.confirmed ? 'badge-odds-confirmed' : 'badge-odds-unconfirmed'}`}
                              >
                                {item.confirmed ? '已确认' : '待确认'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))
              ) : (
                <div className="odds-summary">暂无赔率数据，请点击"刷新赔率"从平台获取</div>
              )}

              {/* Actions */}
              <div className="odds-actions-row">
                <button
                  type="button"
                  className="action-btn action-btn-refresh-odds"
                  onClick={handleRefreshOdds}
                  disabled={refreshLoading || isActioning}
                >
                  {refreshLoading ? '获取中...' : '刷新赔率'}
                </button>
                {oddsStatus === 'unconfirmed' && (
                  <button
                    type="button"
                    className="action-btn action-btn-confirm-odds"
                    onClick={handleConfirmOdds}
                    disabled={oddsLoading || isActioning}
                    style={{ fontSize: 12, padding: '6px 12px', minHeight: 36 }}
                  >
                    {oddsLoading ? '确认中...' : '确认赔率'}
                  </button>
                )}
              </div>

              {/* Refresh Message */}
              {refreshMsg && (
                <div
                  className={`odds-refresh-msg odds-refresh-msg-${refreshMsg.type}`}
                >
                  {refreshMsg.text}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Kill Switch */}
      <div className="kill-switch-row">
        <div>
          <span className="kill-switch-label">熔断开关</span>{' '}
          <span
            className={
              account.kill_switch
                ? 'kill-switch-status kill-switch-status-active'
                : 'kill-switch-status kill-switch-status-normal'
            }
          >
            {account.kill_switch ? '已熔断' : '正常'}
          </span>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={account.kill_switch}
          aria-label={`熔断开关 - ${account.account_name}`}
          className="kill-switch"
          onClick={() => onKillSwitch(account.id, account.kill_switch)}
          disabled={isActioning}
        >
          <span className="kill-switch-knob" />
        </button>
      </div>

      <div className="account-actions">
        {summaryState === 'verified' ? (
          <button
            type="button"
            className="action-btn action-btn-delete"
            onClick={() => onLogout(account.id)}
            disabled={isActioning}
          >
            {actionLoading === 'logout' ? '退出中...' : '退出登录'}
          </button>
        ) : (
          <button
            type="button"
            className="action-btn action-btn-login"
            onClick={() => onVerify(account.id)}
            disabled={isActioning || isVerifying}
          >
            {isVerifying ? '验证中...' : verifyButtonLabel}
          </button>
        )}
        <button
          type="button"
          className="action-btn action-btn-delete"
          onClick={() => onDelete(account.id, account.account_name)}
          disabled={isActioning}
        >
          {actionLoading === 'delete' ? '解绑中...' : '解绑'}
        </button>
        {onCreateStrategy && (
          <button
            type="button"
            className="action-btn action-btn-edit"
            onClick={() => onCreateStrategy(account.id)}
            disabled={isActioning || isVerifying || !canCreateStrategy}
          >
            去创建策略
          </button>
        )}
      </div>
    </div>
  );
}
