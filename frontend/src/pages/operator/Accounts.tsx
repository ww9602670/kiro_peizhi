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
  loginAccount,
  updateKillSwitch,
} from '@/api/accounts';
import { getAccountOdds, confirmAccountOdds, refreshAccountOdds } from '@/api/odds';
import type { AccountCreate, AccountInfo } from '@/types/api/account';
import type { OddsItem, OddsRefreshResponse, PeriodInfo } from '@/types/api/odds';
import './Accounts.css';

const PLATFORM_OPTIONS: { value: AccountCreate['platform_type']; label: string }[] = [
  { value: 'JND28WEB', label: 'JND网盘' },
  { value: 'JND282', label: 'JND2.0' },
];

function getStatusBadgeClass(status: string): string {
  switch (status) {
    case 'online':
      return 'badge-status-online';
    case 'inactive':
      return 'badge-status-inactive';
    case 'login_error':
      return 'badge-status-error';
    case 'disabled':
      return 'badge-status-disabled';
    default:
      return 'badge-status-inactive';
  }
}

function getStatusLabel(status: string): string {
  switch (status) {
    case 'online':
      return '在线';
    case 'inactive':
      return '未登录';
    case 'login_error':
      return '登录异常';
    case 'disabled':
      return '已禁用';
    default:
      return status;
  }
}

export default function Accounts() {
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);

  // Bind form state
  const [formName, setFormName] = useState('');
  const [formPassword, setFormPassword] = useState('');
  const [formPlatform, setFormPlatform] = useState<AccountCreate['platform_type']>('JND28WEB');
  const [formError, setFormError] = useState('');
  const [formLoading, setFormLoading] = useState(false);

  // Per-account action loading
  const [actionLoading, setActionLoading] = useState<Record<number, string>>({});

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

    setFormLoading(true);
    try {
      const data: AccountCreate = {
        account_name: formName.trim(),
        password: formPassword,
        platform_type: formPlatform,
      };
      await createAccount(data);
      // Reset form & refresh list
      setFormName('');
      setFormPassword('');
      setFormPlatform('JND28WEB');
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

  const handleLogin = async (id: number) => {
    setActionLoading((prev) => ({ ...prev, [id]: 'login' }));
    try {
      await loginAccount(id);
      await fetchAccounts();
    } catch (err) {
      if (isApiError(err)) {
        alert(err.message);
      } else {
        alert('登录失败');
      }
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`确定解绑账号「${name}」？`)) return;
    setActionLoading((prev) => ({ ...prev, [id]: 'delete' }));
    try {
      await deleteAccount(id);
      await fetchAccounts();
    } catch (err) {
      if (isApiError(err)) {
        alert(err.message);
      } else {
        alert('解绑失败');
      }
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
      if (isApiError(err)) {
        alert(err.message);
      } else {
        alert('操作失败');
      }
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
              <label htmlFor="bind-platform" className="bind-label">
                盘口类型
              </label>
              <select
                id="bind-platform"
                className="bind-select"
                value={formPlatform}
                onChange={(e) =>
                  setFormPlatform(e.target.value as AccountCreate['platform_type'])
                }
                disabled={formLoading}
              >
                {PLATFORM_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="bind-actions">
              <button
                type="button"
                className="bind-cancel-btn"
                onClick={() => {
                  setShowForm(false);
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
              onLogin={handleLogin}
              onDelete={handleDelete}
              onKillSwitch={handleKillSwitch}
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
  onLogin: (id: number) => void;
  onDelete: (id: number, name: string) => void;
  onKillSwitch: (id: number, currentEnabled: boolean) => void;
}

function AccountCard({
  account,
  actionLoading,
  onLogin,
  onDelete,
  onKillSwitch,
}: AccountCardProps) {
  const isActioning = !!actionLoading;
  const [oddsStatus, setOddsStatus] = useState<OddsStatus>('none');
  const [oddsItems, setOddsItems] = useState<OddsItem[]>([]);
  const [oddsLoading, setOddsLoading] = useState(false);
  const [oddsExpanded, setOddsExpanded] = useState(false);
  const [refreshLoading, setRefreshLoading] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<{ text: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [periodInfo, setPeriodInfo] = useState<PeriodInfo | null>(null);

  const fetchOddsStatus = useCallback(async () => {
    if (account.status !== 'online') {
      setOddsStatus('none');
      setOddsItems([]);
      return;
    }
    try {
      const res = await getAccountOdds(account.id);
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
  }, [account.id, account.status]);

  useEffect(() => {
    fetchOddsStatus();
  }, [fetchOddsStatus]);

  const handleConfirmOdds = async () => {
    setOddsLoading(true);
    try {
      await confirmAccountOdds(account.id);
      await fetchOddsStatus();
      setRefreshMsg({ text: '赔率已确认', type: 'success' });
    } catch (err) {
      if (isApiError(err)) {
        alert(err.message);
      } else {
        alert('确认赔率失败');
      }
    } finally {
      setOddsLoading(false);
    }
  };

  const handleRefreshOdds = async () => {
    setRefreshLoading(true);
    setRefreshMsg(null);
    try {
      const res = await refreshAccountOdds(account.id);
      const data = res.data as OddsRefreshResponse | undefined;
      if (data) {
        if (data.period) setPeriodInfo(data.period);
        if (data.synced) {
          setRefreshMsg({ text: data.message, type: 'success' });
          await fetchOddsStatus();
        } else if (data.odds_count > 0) {
          setRefreshMsg({ text: data.message, type: 'error' });
        } else {
          setRefreshMsg({ text: data.message, type: 'info' });
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
          <span className="badge badge-platform">{account.platform_type}</span>
          <span className={`badge ${getStatusBadgeClass(account.status)}`}>
            {getStatusLabel(account.status)}
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
          <span className="account-info-label">赔率状态</span>
          <span className={`badge ${getOddsBadgeClass(oddsStatus)}`}>
            {getOddsLabel(oddsStatus)}
          </span>
        </div>
        {oddsItems.length > 0 && (
          <div className="account-info-item">
            <span className="account-info-label">赔率数量</span>
            <span className="account-info-value">{oddsItems.length} 项</span>
          </div>
        )}
      </div>

      {/* Odds Detail Panel */}
      {account.status === 'online' && (
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
        <button
          type="button"
          className="action-btn action-btn-login"
          onClick={() => onLogin(account.id)}
          disabled={isActioning}
        >
          {actionLoading === 'login' ? '登录中...' : '登录'}
        </button>
        <button
          type="button"
          className="action-btn action-btn-delete"
          onClick={() => onDelete(account.id, account.account_name)}
          disabled={isActioning}
        >
          {actionLoading === 'delete' ? '解绑中...' : '解绑'}
        </button>
      </div>
    </div>
  );
}
