/**
 * Strategy create/edit form.
 */

import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { isApiError } from '@/api/request';
import { createStrategy, updateStrategy } from '@/api/strategies';
import { listAccounts } from '@/api/accounts';
import PlayCodeMultiSelect from '@/components/PlayCodeMultiSelect';
import type { StrategyCreate, StrategyInfo, StrategyUpdate } from '@/types/api/strategy';
import type { AccountInfo } from '@/types/api/account';
import './StrategyForm.css';

type StrategyType = 'flat' | 'martin' | 'red_wave_double_martin';

interface StrategyFormProps {
  strategy: StrategyInfo | null;
  onDone: () => void;
  onCancel: () => void;
}

const RED_WAVE_DOUBLE_TYPE: StrategyType = 'red_wave_double_martin';
const RED_WAVE_DIRECTION_ORDER = ['B1LM_S', 'B2LM_S', 'B3LM_S', 'DS4'] as const;
const RED_WAVE_DIRECTION_OPTIONS = [
  { code: 'B1LM_S', label: '球1' },
  { code: 'B2LM_S', label: '球2' },
  { code: 'B3LM_S', label: '球3' },
  { code: 'DS4', label: '和值' },
] as const;

function normalizeRedWaveDirections(codes: string[]): string[] {
  const upper = codes.map((code) => code.trim().toUpperCase()).filter(Boolean);
  const deduped = Array.from(new Set(upper));
  return RED_WAVE_DIRECTION_ORDER.filter((code) => deduped.includes(code));
}

function isMartinLike(type: StrategyType) {
  return type === 'martin' || type === RED_WAVE_DOUBLE_TYPE;
}

export default function StrategyForm({ strategy, onDone, onCancel }: StrategyFormProps) {
  const isEdit = !!strategy;

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);

  const [accountId, setAccountId] = useState<number>(strategy?.account_id ?? 0);
  const [name, setName] = useState(strategy?.name ?? '');
  const [type, setType] = useState<StrategyType>(
    (strategy?.type as StrategyType) ?? 'flat'
  );
  const [playCode, setPlayCode] = useState<string[]>(
    strategy?.play_code ? strategy.play_code.split(',') : []
  );
  const [redWaveDirections, setRedWaveDirections] = useState<string[]>(
    (() => {
      if (strategy?.type !== RED_WAVE_DOUBLE_TYPE) return ['DS4'];
      const normalized = normalizeRedWaveDirections(strategy.play_code.split(','));
      return normalized.length > 0 ? normalized : ['DS4'];
    })()
  );
  const [platformType, setPlatformType] = useState<string>(
    strategy?.platform_type ?? 'JND28WEB'
  );
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

  const isRedWaveDouble = type === RED_WAVE_DOUBLE_TYPE;

  const fetchAccounts = useCallback(async () => {
    try {
      const res = await listAccounts();
      const list = res.data ?? [];
      setAccounts(list);
      if (!isEdit && list.length > 0 && accountId === 0) {
        setAccountId(list[0].id);
        setPlatformType(list[0].platform_type || 'JND28WEB');
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

  const handleAccountChange = (nextAccountId: number) => {
    setAccountId(nextAccountId);
    const account = accounts.find((a) => a.id === nextAccountId);
    if (account?.platform_type) {
      setPlatformType(account.platform_type);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError('');

    if (!name.trim()) { setFormError('请输入策略名称'); return; }
    if (!baseAmount || Number(baseAmount) <= 0) { setFormError('基础金额必须大于 0'); return; }
    if (!betTiming || Number(betTiming) < 5 || Number(betTiming) > 180) {
      setFormError('下注时机须在 5-180 秒之间'); return;
    }
    if (!isRedWaveDouble && playCode.length === 0) {
      setFormError('请选择玩法'); return;
    }
    const selectedDirections = normalizeRedWaveDirections(redWaveDirections);
    if (isRedWaveDouble && selectedDirections.length === 0) {
      setFormError('红波追双至少选择一个方向'); return;
    }

    let parsedSequence: number[] | null = null;
    if (isMartinLike(type)) {
      const parts = martinSequence.split(',').map((s) => s.trim()).filter(Boolean);
      if (parts.length === 0) { setFormError('马丁序列不能为空'); return; }
      parsedSequence = parts.map(Number);
      if (parsedSequence.some((n) => Number.isNaN(n) || n <= 0)) {
        setFormError('马丁序列必须为正数，逗号分隔'); return;
      }
    }

    setSubmitting(true);
    try {
      if (isEdit && strategy) {
        const updatePayload: StrategyUpdate = {
          name: name.trim(),
          base_amount: Number(baseAmount),
          martin_sequence: parsedSequence,
          bet_timing: Number(betTiming),
          simulation,
          stop_loss: stopLoss ? Number(stopLoss) : null,
          take_profit: takeProfit ? Number(takeProfit) : null,
          platform_type: platformType,
        };
        if (isRedWaveDouble) {
          updatePayload.play_code = selectedDirections.join(',');
        }
        await updateStrategy(strategy.id, updatePayload);
      } else {
        const data: StrategyCreate = {
          account_id: accountId,
          name: name.trim(),
          type,
          play_code: isRedWaveDouble
            ? selectedDirections.join(',')
            : playCode.join(','),
          base_amount: Number(baseAmount),
          martin_sequence: parsedSequence,
          bet_timing: Number(betTiming),
          simulation,
          stop_loss: stopLoss ? Number(stopLoss) : null,
          take_profit: takeProfit ? Number(takeProfit) : null,
          platform_type: platformType,
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

  const toggleRedWaveDirection = (code: string) => {
    setRedWaveDirections((previous) => {
      if (previous.includes(code)) {
        return previous.filter((item) => item !== code);
      }
      return normalizeRedWaveDirections([...previous, code]);
    });
  };

  return (
    <div className="strategy-form-page">
      <form className="strategy-form" onSubmit={handleSubmit}>
        <div className="strategy-form-header">
          <h2 className="strategy-form-title">{isEdit ? '编辑策略' : '创建策略'}</h2>
        </div>

        {formError && <div role="alert" className="form-error">{formError}</div>}

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
                onChange={(e) => handleAccountChange(Number(e.target.value))}
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
            id="sf-name"
            type="text"
            className="form-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：红波追双"
            disabled={submitting}
            autoComplete="off"
          />
        </div>

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
              <button
                type="button"
                className={`type-toggle-btn ${isRedWaveDouble ? 'type-toggle-active' : ''}`}
                onClick={() => {
                  setType(RED_WAVE_DOUBLE_TYPE);
                  setRedWaveDirections((current) => (
                    current.length > 0 ? current : ['DS4']
                  ));
                }}
                disabled={submitting}
              >
                红波追双
              </button>
            </div>
          </div>
        )}

        {isRedWaveDouble && (
          <div className="form-field">
            <span className="form-label">检测方向</span>
            <div className="direction-grid">
              {RED_WAVE_DIRECTION_OPTIONS.map((option) => {
                const checked = redWaveDirections.includes(option.code);
                return (
                  <label key={option.code} className="direction-option">
                    <input
                      type="checkbox"
                      className="direction-checkbox"
                      checked={checked}
                      onChange={() => toggleRedWaveDirection(option.code)}
                      disabled={submitting}
                    />
                    <span className="direction-text">{option.label}</span>
                  </label>
                );
              })}
            </div>
            <div className="form-hint">至少选择一个方向，默认和值。</div>
          </div>
        )}

        {!isEdit && !isRedWaveDouble && (
          <div className="form-field">
            <label className="form-label">玩法</label>
            <PlayCodeMultiSelect
              value={playCode}
              onChange={setPlayCode}
              disabled={submitting}
            />
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-platform" className="form-label">盘口类型</label>
          <select
            id="sf-platform"
            className="form-select"
            value={platformType}
            onChange={(e) => setPlatformType(e.target.value)}
            disabled={submitting}
          >
            <option value="JND28WEB">JND28WEB</option>
            <option value="JND282">JND282</option>
          </select>
        </div>

        <div className="form-field">
          <label htmlFor="sf-amount" className="form-label">基础金额（元）</label>
          <input
            id="sf-amount"
            type="number"
            className="form-input"
            inputMode="decimal"
            value={baseAmount}
            onChange={(e) => setBaseAmount(e.target.value)}
            placeholder="例如：10"
            min="0.01"
            step="0.01"
            disabled={submitting}
          />
        </div>

        {isMartinLike(type) && (
          <div className="form-field">
            <label htmlFor="sf-martin" className="form-label">马丁倍率序列</label>
            <input
              id="sf-martin"
              type="text"
              className="form-input"
              value={martinSequence}
              onChange={(e) => setMartinSequence(e.target.value)}
              placeholder="逗号分隔，例如：1,2,4,8,16"
              disabled={submitting}
              autoComplete="off"
            />
            <div className="form-hint">实际金额 = 基础金额 × 当前倍率</div>
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-timing" className="form-label">下注时机（秒）</label>
          <input
            id="sf-timing"
            type="number"
            className="form-input"
            inputMode="numeric"
            value={betTiming}
            onChange={(e) => setBetTiming(e.target.value)}
            min="5"
            max="180"
            disabled={submitting}
          />
          <div className="form-hint">开盘后多少秒下注（5-180）</div>
        </div>

        <div className="form-field">
          <div className="toggle-row">
            <span className="form-label">模拟模式</span>
            <button
              type="button"
              role="switch"
              aria-checked={simulation}
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
            id="sf-stoploss"
            type="number"
            className="form-input"
            inputMode="decimal"
            value={stopLoss}
            onChange={(e) => setStopLoss(e.target.value)}
            placeholder="留空表示不设置"
            min="0.01"
            step="0.01"
            disabled={submitting}
          />
        </div>

        <div className="form-field">
          <label htmlFor="sf-takeprofit" className="form-label">止盈线（元，可选）</label>
          <input
            id="sf-takeprofit"
            type="number"
            className="form-input"
            inputMode="decimal"
            value={takeProfit}
            onChange={(e) => setTakeProfit(e.target.value)}
            placeholder="留空表示不设置"
            min="0.01"
            step="0.01"
            disabled={submitting}
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
