/**
 * 策略创建/编辑表单（Mobile-First）
 * - 平注/马丁切换
 * - 马丁序列输入（逗号分隔）
 * - 模拟模式开关
 * - 账号选择（从 listAccounts 获取）
 */

import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { isApiError } from '@/api/request';
import { createStrategy, updateStrategy } from '@/api/strategies';
import { listAccounts } from '@/api/accounts';
import type { StrategyCreate, StrategyInfo } from '@/types/api/strategy';
import type { AccountInfo } from '@/types/api/account';
import './StrategyForm.css';

interface StrategyFormProps {
  strategy: StrategyInfo | null; // null = create mode
  onDone: () => void;
  onCancel: () => void;
}

export default function StrategyForm({ strategy, onDone, onCancel }: StrategyFormProps) {
  const isEdit = !!strategy;

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);

  // Form fields
  const [accountId, setAccountId] = useState<number>(strategy?.account_id ?? 0);
  const [name, setName] = useState(strategy?.name ?? '');
  const [type, setType] = useState<'flat' | 'martin'>(
    (strategy?.type as 'flat' | 'martin') ?? 'flat'
  );
  const [playCode, setPlayCode] = useState(strategy?.play_code ?? '');
  const [baseAmount, setBaseAmount] = useState(strategy?.base_amount?.toString() ?? '');
  const [martinSequence, setMartinSequence] = useState(
    strategy?.martin_sequence?.join(',') ?? '1,2,4,8,16'
  );
  const [betTiming, setBetTiming] = useState(strategy?.bet_timing?.toString() ?? '30');
  const [simulation, setSimulation] = useState(strategy?.simulation ?? false);
  const [stopLoss, setStopLoss] = useState(strategy?.stop_loss?.toString() ?? '');
  const [takeProfit, setTakeProfit] = useState(strategy?.take_profit?.toString() ?? '');

  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchAccounts = useCallback(async () => {
    try {
      const res = await listAccounts();
      const list = res.data ?? [];
      setAccounts(list);
      if (!isEdit && list.length > 0 && accountId === 0) {
        setAccountId(list[0].id);
      }
    } catch (err) {
      if (isApiError(err)) setFormError('加载账号列表失败: ' + err.message);
    } finally {
      setAccountsLoading(false);
    }
  }, [isEdit, accountId]);

  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError('');

    if (!name.trim()) { setFormError('请输入策略名称'); return; }
    if (!playCode.trim()) { setFormError('请输入玩法代码'); return; }
    if (!baseAmount || Number(baseAmount) <= 0) { setFormError('基础金额必须大于 0'); return; }
    if (!betTiming || Number(betTiming) < 5 || Number(betTiming) > 180) {
      setFormError('下注时机须在 5-180 秒之间'); return;
    }

    let parsedSequence: number[] | null = null;
    if (type === 'martin') {
      const parts = martinSequence.split(',').map((s) => s.trim()).filter(Boolean);
      if (parts.length === 0) { setFormError('马丁序列不能为空'); return; }
      parsedSequence = parts.map(Number);
      if (parsedSequence.some((n) => isNaN(n) || n <= 0)) {
        setFormError('马丁序列必须为正数，逗号分隔'); return;
      }
    }

    setSubmitting(true);
    try {
      if (isEdit && strategy) {
        await updateStrategy(strategy.id, {
          name: name.trim(),
          base_amount: Number(baseAmount),
          martin_sequence: parsedSequence,
          bet_timing: Number(betTiming),
          simulation,
          stop_loss: stopLoss ? Number(stopLoss) : null,
          take_profit: takeProfit ? Number(takeProfit) : null,
        });
      } else {
        const data: StrategyCreate = {
          account_id: accountId,
          name: name.trim(),
          type,
          play_code: playCode.trim().toUpperCase(),
          base_amount: Number(baseAmount),
          martin_sequence: parsedSequence,
          bet_timing: Number(betTiming),
          simulation,
          stop_loss: stopLoss ? Number(stopLoss) : null,
          take_profit: takeProfit ? Number(takeProfit) : null,
        };
        await createStrategy(data);
      }
      onDone();
    } catch (err) {
      if (isApiError(err)) setFormError(err.message);
      else setFormError('提交失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="strategy-form-page">
      <form className="strategy-form" onSubmit={handleSubmit}>
        <div className="strategy-form-header">
          <h2 className="strategy-form-title">{isEdit ? '编辑策略' : '创建策略'}</h2>
        </div>

        {formError && <div role="alert" className="form-error">{formError}</div>}

        {/* Account selector (create only) */}
        {!isEdit && (
          <div className="form-field">
            <label htmlFor="sf-account" className="form-label">博彩账号</label>
            {accountsLoading ? (
              <div className="form-hint">加载账号中...</div>
            ) : accounts.length === 0 ? (
              <div className="form-hint form-hint-warn">请先绑定博彩账号</div>
            ) : (
              <select
                id="sf-account"
                className="form-select"
                value={accountId}
                onChange={(e) => setAccountId(Number(e.target.value))}
                disabled={submitting}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.account_name} ({a.platform_type})
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-name" className="form-label">策略名称</label>
          <input
            id="sf-name" type="text" className="form-input"
            value={name} onChange={(e) => setName(e.target.value)}
            placeholder="例如：大小平注" disabled={submitting} autoComplete="off"
          />
        </div>

        {/* Type toggle (create only) */}
        {!isEdit && (
          <div className="form-field">
            <span className="form-label">策略类型</span>
            <div className="type-toggle">
              <button
                type="button"
                className={`type-toggle-btn ${type === 'flat' ? 'type-toggle-active' : ''}`}
                onClick={() => setType('flat')}
                disabled={submitting}
              >
                平注
              </button>
              <button
                type="button"
                className={`type-toggle-btn ${type === 'martin' ? 'type-toggle-active' : ''}`}
                onClick={() => setType('martin')}
                disabled={submitting}
              >
                马丁
              </button>
            </div>
          </div>
        )}

        {/* Play code (create only) */}
        {!isEdit && (
          <div className="form-field">
            <label htmlFor="sf-playcode" className="form-label">玩法代码</label>
            <input
              id="sf-playcode" type="text" className="form-input"
              value={playCode} onChange={(e) => setPlayCode(e.target.value)}
              placeholder="例如：DX1, DX2, DS3, DS4" disabled={submitting} autoComplete="off"
            />
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-amount" className="form-label">基础金额（元）</label>
          <input
            id="sf-amount" type="number" className="form-input" inputMode="decimal"
            value={baseAmount} onChange={(e) => setBaseAmount(e.target.value)}
            placeholder="例如：10" min="0.01" step="0.01" disabled={submitting}
          />
        </div>

        {/* Martin sequence */}
        {type === 'martin' && (
          <div className="form-field">
            <label htmlFor="sf-martin" className="form-label">马丁倍率序列</label>
            <input
              id="sf-martin" type="text" className="form-input"
              value={martinSequence} onChange={(e) => setMartinSequence(e.target.value)}
              placeholder="逗号分隔，例如：1,2,4,8,16" disabled={submitting} autoComplete="off"
            />
            <div className="form-hint">倍率序列，实际金额 = 基础金额 × 倍率</div>
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-timing" className="form-label">下注时机（秒）</label>
          <input
            id="sf-timing" type="number" className="form-input" inputMode="numeric"
            value={betTiming} onChange={(e) => setBetTiming(e.target.value)}
            min="5" max="180" disabled={submitting}
          />
          <div className="form-hint">开盘后多少秒下注（5-180）</div>
        </div>

        {/* Simulation toggle */}
        <div className="form-field">
          <div className="toggle-row">
            <span className="form-label">模拟模式</span>
            <button
              type="button" role="switch" aria-checked={simulation}
              className="sim-switch"
              onClick={() => setSimulation(!simulation)}
              disabled={submitting}
            >
              <span className="sim-switch-knob" />
            </button>
          </div>
          <div className="form-hint">模拟模式不实际下注，仅记录虚拟注单</div>
        </div>

        <div className="form-field">
          <label htmlFor="sf-stoploss" className="form-label">止损线（元，可选）</label>
          <input
            id="sf-stoploss" type="number" className="form-input" inputMode="decimal"
            value={stopLoss} onChange={(e) => setStopLoss(e.target.value)}
            placeholder="留空表示不设置" min="0.01" step="0.01" disabled={submitting}
          />
        </div>

        <div className="form-field">
          <label htmlFor="sf-takeprofit" className="form-label">止盈线（元，可选）</label>
          <input
            id="sf-takeprofit" type="number" className="form-input" inputMode="decimal"
            value={takeProfit} onChange={(e) => setTakeProfit(e.target.value)}
            placeholder="留空表示不设置" min="0.01" step="0.01" disabled={submitting}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="form-cancel-btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="form-submit-btn" disabled={submitting || (accounts.length === 0 && !isEdit)}>
            {submitting ? '提交中...' : isEdit ? '保存' : '创建'}
          </button>
        </div>
      </form>
    </div>
  );
}
