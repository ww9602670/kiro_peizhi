/**
 * 策略管理页面（Mobile-First）
 * - 策略列表（卡片布局）
 * - 状态标签、操作按钮
 * - 创建/编辑表单
 */

import { useCallback, useEffect, useState } from 'react';
import { isApiError } from '@/api/request';
import {
  listStrategies,
  deleteStrategy,
  startStrategy,
  pauseStrategy,
  stopStrategy,
} from '@/api/strategies';
import type { StrategyInfo } from '@/types/api/strategy';
import StrategyStatusTag from '@/components/StrategyStatusTag';
import { CountdownDisplay } from '@/components/CountdownDisplay';
import StrategyForm from './StrategyForm';
import './Strategies.css';

function getTypeBadge(type: string): { label: string; className: string } {
  if (type === 'martin') return { label: '马丁', className: 'type-badge-martin' };
  return { label: '平注', className: 'type-badge-flat' };
}

export default function Strategies() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editingStrategy, setEditingStrategy] = useState<StrategyInfo | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<number, string>>({});

  const fetchStrategies = useCallback(async () => {
    try {
      setError('');
      const res = await listStrategies();
      setStrategies(res.data ?? []);
    } catch (err) {
      if (isApiError(err)) setError(err.message);
      else setError('加载策略列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  const withActionLoading = async (id: number, action: string, fn: () => Promise<void>) => {
    setActionLoading((prev) => ({ ...prev, [id]: action }));
    try {
      await fn();
      await fetchStrategies();
    } catch (err) {
      alert(isApiError(err) ? err.message : '操作失败');
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const handleStart = (id: number) => withActionLoading(id, 'start', () => startStrategy(id).then(() => {}));
  const handlePause = (id: number) => withActionLoading(id, 'pause', () => pauseStrategy(id).then(() => {}));
  const handleStop = (id: number) => withActionLoading(id, 'stop', () => stopStrategy(id).then(() => {}));

  const handleDelete = (id: number, name: string) => {
    if (!confirm(`确定删除策略「${name}」？`)) return;
    withActionLoading(id, 'delete', async () => {
      try {
        await deleteStrategy(id);
      } catch (err) {
        if (isApiError(err) && err.message.includes('投注记录')) {
          if (confirm(`${err.message}\n\n是否强制删除（同时删除关联的投注记录）？`)) {
            await deleteStrategy(id, true);
            return;
          }
          throw err;
        }
        throw err;
      }
    });
  };

  const handleEdit = (strategy: StrategyInfo) => {
    setEditingStrategy(strategy);
    setShowForm(true);
  };

  const handleCreate = () => {
    setEditingStrategy(null);
    setShowForm(true);
  };

  const handleFormDone = () => {
    setShowForm(false);
    setEditingStrategy(null);
    fetchStrategies();
  };

  const handleFormCancel = () => {
    setShowForm(false);
    setEditingStrategy(null);
  };

  if (showForm) {
    return (
      <StrategyForm
        strategy={editingStrategy}
        onDone={handleFormDone}
        onCancel={handleFormCancel}
      />
    );
  }

  return (
    <div className="strategies-page">
      <div className="strategies-header">
        <h1 className="strategies-title">投注策略</h1>
      </div>

      <div className="create-section">
        <button className="create-btn" onClick={handleCreate} type="button">
          + 创建策略
        </button>
      </div>

      {/* 彩票倒计时 */}
      <div className="countdown-section">
        <CountdownDisplay />
      </div>

      {loading && <div className="strategies-loading">加载中...</div>}

      {!loading && error && (
        <div className="strategies-error" role="alert">{error}</div>
      )}

      {!loading && !error && strategies.length === 0 && (
        <div className="strategies-empty">暂无策略，点击上方按钮创建</div>
      )}

      {!loading && !error && strategies.length > 0 && (
        <div className="strategy-list">
          {strategies.map((s) => (
            <StrategyCard
              key={s.id}
              strategy={s}
              actionLoading={actionLoading[s.id]}
              onStart={handleStart}
              onPause={handlePause}
              onStop={handleStop}
              onDelete={handleDelete}
              onEdit={handleEdit}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* --- StrategyCard sub-component --- */

interface StrategyCardProps {
  strategy: StrategyInfo;
  actionLoading?: string;
  onStart: (id: number) => void;
  onPause: (id: number) => void;
  onStop: (id: number) => void;
  onDelete: (id: number, name: string) => void;
  onEdit: (strategy: StrategyInfo) => void;
}

function StrategyCard({
  strategy,
  actionLoading,
  onStart,
  onPause,
  onStop,
  onDelete,
  onEdit,
}: StrategyCardProps) {
  const isActioning = !!actionLoading;
  const typeBadge = getTypeBadge(strategy.type);

  const pnlClass = (val: number) =>
    val > 0 ? 'pnl-positive' : val < 0 ? 'pnl-negative' : '';

  return (
    <div className="strategy-card">
      <div className="strategy-card-header">
        <h3 className="strategy-name">{strategy.name}</h3>
        <div className="strategy-badges">
          <span className={`badge ${typeBadge.className}`}>{typeBadge.label}</span>
          <StrategyStatusTag status={strategy.status} />
        </div>
      </div>

      <div className="strategy-info">
        <div className="strategy-info-item">
          <span className="strategy-info-label">玩法</span>
          <span className="strategy-info-value">{strategy.play_code}</span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">基础金额</span>
          <span className="strategy-info-value">{strategy.base_amount.toFixed(2)} 元</span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">当日盈亏</span>
          <span className={`strategy-info-value ${pnlClass(strategy.daily_pnl)}`}>
            {strategy.daily_pnl >= 0 ? '+' : ''}{strategy.daily_pnl.toFixed(2)}
          </span>
        </div>
        <div className="strategy-info-item">
          <span className="strategy-info-label">总盈亏</span>
          <span className={`strategy-info-value ${pnlClass(strategy.total_pnl)}`}>
            {strategy.total_pnl >= 0 ? '+' : ''}{strategy.total_pnl.toFixed(2)}
          </span>
        </div>
      </div>

      {strategy.simulation && (
        <div className="simulation-badge">模拟模式</div>
      )}

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
            <button type="button" className="action-btn action-btn-pause" onClick={() => onPause(strategy.id)} disabled={isActioning}>
              {actionLoading === 'pause' ? '暂停中...' : '暂停'}
            </button>
            <button type="button" className="action-btn action-btn-stop" onClick={() => onStop(strategy.id)} disabled={isActioning}>
              {actionLoading === 'stop' ? '停止中...' : '停止'}
            </button>
          </>
        )}
        {strategy.status === 'paused' && (
          <>
            <button type="button" className="action-btn action-btn-start" onClick={() => onStart(strategy.id)} disabled={isActioning}>
              {actionLoading === 'start' ? '启动中...' : '启动'}
            </button>
            <button type="button" className="action-btn action-btn-stop" onClick={() => onStop(strategy.id)} disabled={isActioning}>
              {actionLoading === 'stop' ? '停止中...' : '停止'}
            </button>
          </>
        )}
        {strategy.status === 'error' && (
          <button type="button" className="action-btn action-btn-stop" onClick={() => onStop(strategy.id)} disabled={isActioning}>
            {actionLoading === 'stop' ? '停止中...' : '停止'}
          </button>
        )}
      </div>
    </div>
  );
}
