import { useCallback, useEffect, useRef, useState } from 'react';
import { isApiError } from '@/api/request';
import { listAccounts } from '@/api/accounts';
import { CountdownDisplay } from '@/components/CountdownDisplay';
import {
  deleteStrategy,
  listStrategies,
  pauseStrategy,
  startStrategy,
  stopStrategy,
  updateStrategy,
} from '@/api/strategies';
import ConfirmDialog from '@/components/ConfirmDialog';
import StrategyStatusTag from '@/components/StrategyStatusTag';
import Toast from '@/components/Toast';
import { useConfirm } from '@/hooks/useConfirm';
import { useToast } from '@/hooks/useToast';
import type { StrategyInfo } from '@/types/api/strategy';
import type { AccountInfo } from '@/types/api/account';
import { getPlatformLabel } from '@/utils/platformLabels';
import { getPlayCodeDisplay } from '@/utils/playCodeDisplay';
import Backtest from './Backtest';
import BetOrders from './BetOrders';
import StrategyForm from './StrategyForm';
import './Strategies.css';

function getTypeBadge(type: string): { label: string; className: string } {
  if (type === 'ai_same_random_flat') return { label: 'AI推荐同号平注', className: 'type-badge-flat' };
  if (type === 'ai_same_random_martin') return { label: 'AI推荐同号平注马丁', className: 'type-badge-martin' };
  if (type === 'red_wave_double_martin') return { label: '红波双马丁', className: 'type-badge-martin' };
  if (type === 'green_wave_single_martin') return { label: '绿波追单', className: 'type-badge-martin' };
  if (type === 'omission_random_flat') return { label: '遗漏随机平注', className: 'type-badge-flat' };
  if (type === 'omission_random_martin') return { label: '遗漏随机马丁', className: 'type-badge-martin' };
  if (type === 'ai_random_flat') return { label: 'AI推荐平注', className: 'type-badge-flat' };
  if (type === 'ai_random_martin') return { label: 'AI推荐马丁', className: 'type-badge-martin' };
  if (type === 'martin') return { label: '马丁', className: 'type-badge-martin' };
  return { label: '普通', className: 'type-badge-flat' };
}

function getOmissionRandomConfigDisplay(strategy: StrategyInfo): string {
  if (
    !strategy.type.startsWith('omission_random_') &&
    !strategy.type.startsWith('ai_random_') &&
    !strategy.type.startsWith('ai_same_random_')
  ) return '';
  const pickCount = strategy.strategy_config?.pick_count;
  return pickCount ? `每类${pickCount}个` : '';
}

type StrategyWorkspace = 'list' | 'orders' | 'backtest';

interface StrategiesProps {
  createIntent?: StrategyCreateIntent | null;
  onCreateIntentConsumed?: () => void;
}

interface StrategyCreateIntent {
  accountId?: number;
  nonce: number;
}

const MANUAL_RELOGIN_MESSAGE = '需要人工处理：请前往账号页重新登录后再试。';
const MANUAL_CONFIRM_ODDS_MESSAGE = '需要人工处理：请先确认赔率后再继续。';
const TOAST_MERGE_WINDOW_MS = 1500;
const RELOGIN_HINTS = ['session', 'worker', 'login', 'relogin', 'auth', 'expired', '未登录', '重新登录', '登录失效', '验证'];
const ODDS_HINTS = ['odds', '赔率', '未确认', 'unconfirmed', 'confirm'];
const STRATEGY_NOT_AUTHORIZED_MESSAGE = '当前账号暂未开通策略，请联系管理员';

function hasStrategyPermission(account: AccountInfo): boolean {
  return (account.allowed_strategy_types ?? []).length > 0;
}

function normalizeOperatorMessage(rawMessage: string | null | undefined, fallback: string): string {
  const text = typeof rawMessage === 'string' ? rawMessage.trim() : '';
  if (!text) return fallback;
  const lowerText = text.toLowerCase();
  if (ODDS_HINTS.some((hint) => lowerText.includes(hint))) return MANUAL_CONFIRM_ODDS_MESSAGE;
  if (RELOGIN_HINTS.some((hint) => lowerText.includes(hint))) return MANUAL_RELOGIN_MESSAGE;
  return text;
}

export default function Strategies({ createIntent, onCreateIntentConsumed }: StrategiesProps) {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [strategyGateLoading, setStrategyGateLoading] = useState(true);
  const [strategyGateError, setStrategyGateError] = useState('');
  const [hasAuthorizedStrategyAccount, setHasAuthorizedStrategyAccount] = useState(false);
  const [workspace, setWorkspace] = useState<StrategyWorkspace>('list');
  const [showForm, setShowForm] = useState(false);
  const [editingStrategy, setEditingStrategy] = useState<StrategyInfo | null>(null);
  const [createAccountId, setCreateAccountId] = useState<number | undefined>(undefined);
  const [actionLoading, setActionLoading] = useState<Record<number, string>>({});
  const { confirmState, confirm, notify, handleConfirm, handleCancel } = useConfirm();
  const { messages, removeToast } = useToast();
  const actionLockRef = useRef<Record<number, string>>({});
  const deleteConfirmLockRef = useRef<Set<number>>(new Set());
  const recentToastRef = useRef<Map<string, number>>(new Map());
  const consumedCreateIntentRef = useRef<string | null>(null);

  const showMergedNotice = useCallback(
    (rawMessage: string | null | undefined, fallback: string) => {
      const message = normalizeOperatorMessage(rawMessage, fallback);
      const now = Date.now();
      const lastShownAt = recentToastRef.current.get(message) ?? 0;
      if (now - lastShownAt < TOAST_MERGE_WINDOW_MS) return;
      recentToastRef.current.set(message, now);
      void notify(message, '操作提示');
    },
    [notify]
  );

  const fetchStrategies = useCallback(async () => {
    try {
      setError('');
      const res = await listStrategies();
      setStrategies(res.data ?? []);
    } catch (err) {
      setError(isApiError(err) ? err.message : '加载策略失败，请稍后再试。');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStrategyGate = useCallback(async () => {
    try {
      setStrategyGateError('');
      const res = await listAccounts();
      setHasAuthorizedStrategyAccount((res.data ?? []).some(hasStrategyPermission));
    } catch (err) {
      setHasAuthorizedStrategyAccount(false);
      setStrategyGateError(isApiError(err) ? err.message : '加载账号权限失败，请稍后重试。');
    } finally {
      setStrategyGateLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  useEffect(() => {
    fetchStrategyGate();
  }, [fetchStrategyGate]);

  useEffect(() => {
    if (!createIntent) {
      return;
    }
    if (strategyGateLoading) {
      return;
    }
    const intentKey = `${createIntent.accountId}:${createIntent.nonce}`;
    if (consumedCreateIntentRef.current === intentKey) {
      return;
    }
    consumedCreateIntentRef.current = intentKey;
    if (!hasAuthorizedStrategyAccount) {
      showMergedNotice(STRATEGY_NOT_AUTHORIZED_MESSAGE, STRATEGY_NOT_AUTHORIZED_MESSAGE);
      onCreateIntentConsumed?.();
      return;
    }
    setWorkspace('list');
    setEditingStrategy(null);
    setCreateAccountId(createIntent.accountId);
    setShowForm(true);
    onCreateIntentConsumed?.();
  }, [
    createIntent,
    hasAuthorizedStrategyAccount,
    onCreateIntentConsumed,
    showMergedNotice,
    strategyGateLoading,
  ]);

  const withActionLoadingSafe = useCallback(
    async (id: number, action: string, task: () => Promise<void>, fallbackError: string) => {
      if (actionLockRef.current[id]) return;
      actionLockRef.current[id] = action;
      setActionLoading((prev) => ({ ...prev, [id]: action }));
      try {
        await task();
        await fetchStrategies();
      } catch (err) {
        showMergedNotice(isApiError(err) ? err.message : '', fallbackError);
      } finally {
        delete actionLockRef.current[id];
        setActionLoading((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }
    },
    [fetchStrategies, showMergedNotice]
  );

  const handleStartSafe = (id: number) =>
    withActionLoadingSafe(id, 'start', () => startStrategy(id).then(() => {}), '操作失败，请稍后再试。');

  const handlePauseSafe = (id: number) =>
    withActionLoadingSafe(id, 'pause', () => pauseStrategy(id).then(() => {}), '操作失败，请稍后再试。');

  const handleStopSafe = (id: number) =>
    withActionLoadingSafe(id, 'stop', () => stopStrategy(id).then(() => {}), '操作失败，请稍后再试。');

  const handleSimulationToggleSafe = async (strategy: StrategyInfo) => {
    if (strategy.simulation) {
      const ok = await confirm(
        `确认将策略“${strategy.name}”切换为真实投注吗？切换后启动策略将会向平台真实下注。`,
        '真实投注确认'
      );
      if (!ok) return;
    }
    await withActionLoadingSafe(
      strategy.id,
      'simulation',
      () => updateStrategy(strategy.id, { simulation: !strategy.simulation }).then(() => {}),
      '操作失败，请稍后再试。'
    );
  };

  const handleDeleteSafe = async (id: number, name: string) => {
    if (actionLockRef.current[id] || deleteConfirmLockRef.current.has(id)) return;
    deleteConfirmLockRef.current.add(id);
    try {
      if (!(await confirm(`确认删除策略“${name}”吗？投注记录和盈亏历史会保留。`))) return;
      await withActionLoadingSafe(id, 'delete', async () => {
        await deleteStrategy(id);
      }, '删除失败，请稍后再试。');
    } finally {
      deleteConfirmLockRef.current.delete(id);
    }
  };

  const handleEdit = (strategy: StrategyInfo) => {
    setEditingStrategy(strategy);
    setCreateAccountId(undefined);
    setShowForm(true);
  };

  const handleCreate = () => {
    if (!hasAuthorizedStrategyAccount) {
      showMergedNotice(STRATEGY_NOT_AUTHORIZED_MESSAGE, STRATEGY_NOT_AUTHORIZED_MESSAGE);
      return;
    }
    setWorkspace('list');
    setEditingStrategy(null);
    setCreateAccountId(undefined);
    setShowForm(true);
  };

  const noStrategyPermission = !strategyGateLoading && !hasAuthorizedStrategyAccount;

  const handleFormDone = () => {
    setShowForm(false);
    setEditingStrategy(null);
    setCreateAccountId(undefined);
    void fetchStrategies();
  };

  const handleFormCancel = () => {
    setShowForm(false);
    setEditingStrategy(null);
    setCreateAccountId(undefined);
  };

  if (showForm) {
    return (
      <StrategyForm
        strategy={editingStrategy}
        initialAccountId={createAccountId}
        existingStrategies={strategies}
        onDone={handleFormDone}
        onCancel={handleFormCancel}
      />
    );
  }

  return (
    <div className="strategies-page">
      <Toast messages={messages} onRemove={removeToast} />
      <ConfirmDialog
        open={confirmState.open}
        message={confirmState.message}
        title={confirmState.title}
        showCancel={confirmState.showCancel}
        confirmText={confirmState.confirmText}
        cancelText={confirmState.cancelText}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />

      <section className="strategies-countdown-section">
        {/* Use the first running strategy's platform; fall back to first loaded strategy,
            then JND28WEB as the operator console primary deployment target. */}
        <CountdownDisplay
          platformType={
            strategies.find((s) => s.status === 'running')?.platform_type ??
            strategies[0]?.platform_type ??
            'JND28WEB'
          }
        />
      </section>

      <div className="strategies-header">
        <div>
          <h1 className="strategies-title">投注策略</h1>
          <p className="strategies-subtitle">策略、投注记录和回测统一收口在这一页处理。</p>
        </div>
        {!strategyGateLoading && hasAuthorizedStrategyAccount && (
          <button className="create-btn" onClick={handleCreate} type="button">
            + 创建策略
          </button>
        )}
      </div>

      {strategyGateError && (
        <div className="strategies-error" role="alert">{strategyGateError}</div>
      )}

      <div className="strategy-workspace-tabs" role="tablist" aria-label="策略工作区">
        <button
          type="button"
          role="tab"
          aria-selected={workspace === 'list'}
          className={`workspace-tab ${workspace === 'list' ? 'workspace-tab-active' : ''}`}
          onClick={() => setWorkspace('list')}
        >
          策略列表
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={workspace === 'orders'}
          className={`workspace-tab ${workspace === 'orders' ? 'workspace-tab-active' : ''}`}
          onClick={() => setWorkspace('orders')}
        >
          投注记录
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={workspace === 'backtest'}
          className={`workspace-tab ${workspace === 'backtest' ? 'workspace-tab-active' : ''}`}
          onClick={() => setWorkspace('backtest')}
        >
          回测
        </button>
      </div>

      {workspace === 'orders' && <BetOrders />}
      {workspace === 'backtest' && <Backtest />}

      {workspace === 'list' && (
        <>
          {loading && <div className="strategies-loading">加载中...</div>}

          {!loading && error && (
            <div className="strategies-error" role="alert">{error}</div>
          )}

          {!loading && !error && strategies.length === 0 && (
            <div className="strategies-empty">
              {noStrategyPermission ? STRATEGY_NOT_AUTHORIZED_MESSAGE : '暂无策略，点击上方按钮创建。'}
            </div>
          )}

          {!loading && !error && strategies.length > 0 && (
            <div className="strategy-list">
              {strategies.map((strategy) => (
                <StrategyCard
                  key={strategy.id}
                  strategy={strategy}
                  actionLoading={actionLoading[strategy.id]}
                  onStart={handleStartSafe}
                  onPause={handlePauseSafe}
                  onStop={handleStopSafe}
                  onDelete={handleDeleteSafe}
                  onEdit={handleEdit}
                  onToggleSimulation={handleSimulationToggleSafe}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

interface StrategyCardProps {
  strategy: StrategyInfo;
  actionLoading?: string;
  onStart: (id: number) => void;
  onPause: (id: number) => void;
  onStop: (id: number) => void;
  onDelete: (id: number, name: string) => void;
  onEdit: (strategy: StrategyInfo) => void;
  onToggleSimulation: (strategy: StrategyInfo) => void;
}

function StrategyCard({
  strategy,
  actionLoading,
  onStart,
  onPause,
  onStop,
  onDelete,
  onEdit,
  onToggleSimulation,
}: StrategyCardProps) {
  const isActioning = Boolean(actionLoading);
  const typeBadge = getTypeBadge(strategy.type);
  const omissionConfigDisplay = getOmissionRandomConfigDisplay(strategy);

  const pnlClass = (value: number) => (
    value > 0 ? 'pnl-positive' : value < 0 ? 'pnl-negative' : ''
  );

  const canToggleSimulation = strategy.status !== 'running';

  return (
    <div className="strategy-card">
      <div className="strategy-card-header">
        <div>
          <h3 className="strategy-name">{strategy.name}</h3>
          <p className="strategy-mode-line">
            当前模式：
            <strong>{strategy.simulation ? '模拟' : '真实'}</strong>
          </p>
        </div>
        <div className="strategy-badges">
          <span className={`badge ${typeBadge.className}`}>{typeBadge.label}</span>
          <StrategyStatusTag status={strategy.status} />
        </div>
      </div>

      <div className="strategy-info">
        <div className="strategy-info-item">
          <span className="strategy-info-label">平台</span>
          <span className="strategy-info-value">{getPlatformLabel(strategy.platform_type)}</span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">玩法</span>
          <span className="strategy-info-value">
            {getPlayCodeDisplay(strategy.play_code_name, strategy.play_code)}
            {omissionConfigDisplay ? ` · ${omissionConfigDisplay}` : ''}
          </span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">基础金额</span>
          <span className="strategy-info-value">{strategy.base_amount.toFixed(2)} 元</span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">今日盈亏</span>
          <span className={`strategy-info-value ${pnlClass(strategy.daily_pnl)}`}>
            {strategy.daily_pnl >= 0 ? '+' : ''}{strategy.daily_pnl.toFixed(2)}
          </span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">累计盈亏</span>
          <span className={`strategy-info-value ${pnlClass(strategy.total_pnl)}`}>
            {strategy.total_pnl >= 0 ? '+' : ''}{strategy.total_pnl.toFixed(2)}
          </span>
        </div>
      </div>

      <div className="strategy-actions">
        {strategy.status === 'stopped' && (
          <>
            <button type="button" className="action-btn action-btn-start" onClick={() => onStart(strategy.id)} disabled={isActioning}>
              {actionLoading === 'start' ? '启动中...' : '启动'}
            </button>
            <button type="button" className="action-btn action-btn-edit" onClick={() => onEdit(strategy)} disabled={isActioning}>
              编辑
            </button>
            <button type="button" className="action-btn action-btn-delete" onClick={() => onDelete(strategy.id, strategy.name)} disabled={isActioning}>
              {actionLoading === 'delete' ? '删除中...' : '删除'}
            </button>
          </>
        )}
        {strategy.status === 'running' && (
          <>
            <button
              type="button"
              className="action-btn action-btn-pause"
              onClick={() => onPause(strategy.id)}
              disabled={isActioning}
              title="保留马丁级别，稍后可续跑"
            >
              {actionLoading === 'pause' ? '暂停中...' : '暂停'}
            </button>
            <button
              type="button"
              className="action-btn action-btn-stop"
              onClick={() => onStop(strategy.id)}
              disabled={isActioning}
              title="重置马丁级别，回到初始状态"
            >
              {actionLoading === 'stop' ? '停止中...' : '停止'}
            </button>
          </>
        )}
        {strategy.status === 'paused' && (
          <>
            <button type="button" className="action-btn action-btn-start" onClick={() => onStart(strategy.id)} disabled={isActioning}>
              {actionLoading === 'start' ? '启动中...' : '启动'}
            </button>
            <button
              type="button"
              className="action-btn action-btn-stop"
              onClick={() => onStop(strategy.id)}
              disabled={isActioning}
              title="重置马丁级别，回到初始状态"
            >
              {actionLoading === 'stop' ? '停止中...' : '停止'}
            </button>
          </>
        )}
        {strategy.status === 'error' && (
          <button
            type="button"
            className="action-btn action-btn-stop"
            onClick={() => onStop(strategy.id)}
            disabled={isActioning}
            title="重置马丁级别，回到初始状态"
          >
            {actionLoading === 'stop' ? '停止中...' : '停止'}
          </button>
        )}

        <button
          type="button"
          className="action-btn action-btn-mode"
          onClick={() => onToggleSimulation(strategy)}
          disabled={isActioning || !canToggleSimulation}
          title={canToggleSimulation ? undefined : '运行中不可切换模式，请先暂停或停止'}
        >
          {actionLoading === 'simulation'
            ? '切换中...'
            : strategy.simulation
              ? '切到真实'
              : '切到模拟'}
        </button>
      </div>
    </div>
  );
}
