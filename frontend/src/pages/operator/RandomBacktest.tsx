/**
 * 随机马丁回测页面 — 4 阶段流程
 * Phase 1: 参数配置
 * Phase 2: 方案生成预览
 * Phase 3: 日期选择 + 回测执行
 * Phase 4: 选组 + 创建策略
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { isApiError } from '@/api/request';
import {
  generateAndSavePlan,
  createRandomBacktestTask,
  getRandomBacktestTask,
} from '@/api/randomBacktest';
import { getHistoryStatus, fillHistory } from '@/api/backtest';
import type {
  PlanSchema,
  GeneratePlanResponse,
  GroupResultSchema,
  BacktestTaskInfo,
} from '@/api/randomBacktest';
import type { HistoryDataStatus } from '@/api/backtest';
import type { RandomMartinConfig } from '@/types/api/strategy';
import Toast from '@/components/Toast';
import { useToast } from '@/hooks/useToast';
import './RandomBacktest.css';

type Phase = 1 | 2 | 3 | 4;

interface RandomBacktestProps {
  onCreateStrategy?: (config: RandomMartinConfig) => void;
}

function fmtDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function fmtFen(fen: number): string {
  return (fen / 100).toFixed(2);
}

function comb10(k: number): number {
  if (k < 0 || k > 10) return 0;
  let num = 1, den = 1;
  for (let i = 0; i < k; i++) { num *= (10 - i); den *= (i + 1); }
  return num / den;
}

/** 3×C(10,K)：组内每期可选的不重复 (球号,号码) 组合总数，也是 N 的上限 */
function maxN(K: number): number {
  return 3 * comb10(K);
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function RandomBacktest({ onCreateStrategy }: RandomBacktestProps) {
  const { messages, showToast, removeToast } = useToast();

  const [phase, setPhase] = useState<Phase>(1);

  // Phase 1 params
  const [numGroups, setNumGroups] = useState(100);
  const [periodsN, setPeriodsN] = useState(3);
  const [numbersK, setNumbersK] = useState(5);
  const [chaseM, setChaseM] = useState(6);
  const [baseUnit, setBaseUnit] = useState(1);
  const [martinMultiplier, setMartinMultiplier] = useState(2);
  const [odds, setOdds] = useState(9.927);
  const [fundMode, setFundMode] = useState<'shared_total' | 'per_group'>('per_group');
  const [totalBalance, setTotalBalance] = useState(10000);
  const [perGroupBalance, setPerGroupBalance] = useState(200);

  // N 的上限 = 3×C(10,K)，组数 G 实际上限远超 3000，无需额外限制
  const maxPeriods = maxN(numbersK);
  const periodsNInvalid = periodsN > maxPeriods;

  // Phase 2
  const [planData, setPlanData] = useState<GeneratePlanResponse | null>(null);
  const [generating, setGenerating] = useState(false);

  // Phase 3
  const today = new Date();
  const [startDate, setStartDate] = useState(fmtDate(today));
  const [endDate, setEndDate] = useState(fmtDate(today));
  const [dateError, setDateError] = useState('');
  const [backtestRunning, setBacktestRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [taskResult, setTaskResult] = useState<BacktestTaskInfo | null>(null);
  const [historyStatus, setHistoryStatus] = useState<HistoryDataStatus | null>(null);
  const [filling, setFilling] = useState(false);
  const historyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Phase 4
  const [selectedGroups, setSelectedGroups] = useState<Set<number>>(new Set());
  const [selectTab, setSelectTab] = useState<'survived' | 'busted'>('survived');

  // 多次回测筛选迭代（最多 5 层）
  const MAX_ITERATION_DEPTH = 5;
  const [iterationDepth, setIterationDepth] = useState(0);
  const [filteredPlanIds, setFilteredPlanIds] = useState<number[] | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  // Phase 3 进入时拉取历史状态，syncing 时 3s 轮询
  const fetchHistoryStatus = useCallback(async () => {
    try {
      const res = await getHistoryStatus();
      if (res.data) setHistoryStatus(res.data);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (phase !== 3) return;
    fetchHistoryStatus();
    const interval = historyStatus?.syncing ? 3000 : 30000;
    const timer = setInterval(fetchHistoryStatus, interval);
    historyPollRef.current = timer;
    return () => { clearInterval(timer); historyPollRef.current = null; };
  }, [phase, historyStatus?.syncing, fetchHistoryStatus]);

  const handleFillHistory = async () => {
    setFilling(true);
    try {
      await fillHistory();
      showToast('数据同步已触发，请稍候...');
      await fetchHistoryStatus();
    } catch (err) {
      showToast(isApiError(err) ? err.message : '同步失败');
    } finally {
      setFilling(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Phase 1 → 2: generate plan
  // ---------------------------------------------------------------------------

  const handleGenerate = async () => {
    if (chaseM < periodsN) { showToast(`追投上限 M 必须 >= 期数 N（${periodsN}）`); return; }
    setGenerating(true);
    try {
      const res = await generateAndSavePlan({ num_groups: numGroups, N: periodsN, K: numbersK });
      if (res.data) {
        setPlanData(res.data);
        setPhase(2);
        setSelectedGroups(new Set());
        setTaskResult(null);
        setProgress(0);
      }
    } catch (err) {
      showToast(isApiError(err) ? err.message : '生成方案失败');
    } finally {
      setGenerating(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Phase 3: run backtest
  // ---------------------------------------------------------------------------

  const historyMaxDate = historyStatus?.max_time?.slice(0, 10) ?? null;
  const historyMinDate = historyStatus?.min_time?.slice(0, 10) ?? null;

  const validateDates = (s: string, e: string) => {
    if (!s || !e) { setDateError(''); return true; }
    const sd = new Date(s), ed = new Date(e);
    if (ed < sd) { setDateError('结束日期不能早于开始日期'); return false; }
    const diffDays = (ed.getTime() - sd.getTime()) / 86400000;
    if (diffDays > 1) { setDateError('最多选择 1 天的数据区间'); return false; }
    if (historyMaxDate && s > historyMaxDate) {
      setDateError(`所选日期超出历史数据范围（最新：${historyMaxDate}），请先同步数据`);
      return false;
    }
    if (historyMinDate && e < historyMinDate) {
      setDateError(`所选日期早于历史数据起始日期（${historyMinDate}）`);
      return false;
    }
    setDateError(''); return true;
  };

  const handleStartChange = (v: string) => { setStartDate(v); validateDates(v, endDate); };
  const handleEndChange = (v: string) => { setEndDate(v); validateDates(startDate, v); };

  const handleRunBacktest = async () => {
    if (!planData) return;
    if (!validateDates(startDate, endDate)) return;
    setBacktestRunning(true);
    setProgress(0);
    setTaskResult(null);
    try {
      // 筛选模式：只测 filteredPlanIds 包含的组
      const plansToTest = filteredPlanIds === null
        ? planData.plans
        : planData.plans.filter(p => filteredPlanIds.includes(p.group_id));
      const res = await createRandomBacktestTask({
        config: {
          fund_mode: fundMode,
          initial_total_balance: fundMode === 'shared_total' ? totalBalance : 0,
          initial_balance_per_group: fundMode === 'per_group' ? perGroupBalance : 0,
          base_unit: baseUnit,
          martin_multiplier: martinMultiplier,
          odds,
          chase_limit: chaseM,
        },
        plans: plansToTest,
        plan_set_id: planData.plan_set_id,
        start_date: startDate,
        end_date: endDate,
        kline_window: 50,
      });
      if (res.data) {
        const taskId = res.data.id;
        pollRef.current = setInterval(async () => {
          try {
            const taskRes = await getRandomBacktestTask(taskId);
            if (taskRes.data) {
              const t = taskRes.data;
              if (t.total_issues > 0) {
                setProgress(Math.round((t.processed_issues / t.total_issues) * 100));
              }
              if (t.status === 'completed' || t.status === 'failed') {
                stopPolling();
                setTaskResult(t);
                setBacktestRunning(false);
                if (t.status === 'failed') showToast(t.error_message || '回测失败');
                else setPhase(4);
              }
            }
          } catch { stopPolling(); setBacktestRunning(false); }
        }, 2000);
      }
    } catch (err) {
      showToast(isApiError(err) ? err.message : '启动回测失败');
      setBacktestRunning(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Phase 4: group selection helpers
  // ---------------------------------------------------------------------------

  const groupResults: GroupResultSchema[] = taskResult?.result?.group_results ?? [];
  const survived = groupResults.filter(g => !g.abandoned);
  const busted = groupResults.filter(g => g.abandoned);
  const shownGroups = selectTab === 'survived' ? survived : busted;

  const toggleGroup = (gid: number) => {
    setSelectedGroups(prev => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid); else next.add(gid);
      return next;
    });
  };

  const selectAll = (list: GroupResultSchema[]) =>
    setSelectedGroups(prev => { const n = new Set(prev); list.forEach(g => n.add(g.group_id)); return n; });

  const deselectAll = (list: GroupResultSchema[]) =>
    setSelectedGroups(prev => { const n = new Set(prev); list.forEach(g => n.delete(g.group_id)); return n; });

  const selectedCount = selectedGroups.size;
  const tooFew = selectedCount < 10;
  const tooMany = selectedCount > 1500;
  const selectionValid = !tooFew && !tooMany;

  const handleCreateStrategy = () => {
    if (!planData || !selectionValid) return;
    onCreateStrategy?.({
      plan_set_id: planData.plan_set_id,
      group_ids: Array.from(selectedGroups),
      N: periodsN,
      M: chaseM,
      K: numbersK,
      martin_multiplier: martinMultiplier,
    });
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const result = taskResult?.result;

  return (
    <div className="rb-page">
      <Toast messages={messages} onRemove={removeToast} />
      <h1 className="rb-title">随机马丁回测</h1>

      {/* Step indicator */}
      <div className="rb-steps">
        {([1, 2, 3, 4] as Phase[]).map(s => (
          <div key={s} className={`rb-step ${phase === s ? 'rb-step-active' : phase > s ? 'rb-step-done' : ''}`}>
            <span className="rb-step-num">{s}</span>
            <span className="rb-step-label">
              {s === 1 ? '配置参数' : s === 2 ? '生成方案' : s === 3 ? '执行回测' : '选组建策略'}
            </span>
          </div>
        ))}
      </div>

      {/* Phase 1 */}
      {phase === 1 && (
        <div className="rb-card">
          <h2>参数配置</h2>
          <div className="rb-grid-2">
            <div className="rb-field">
              <label>组数 G（10-3000）</label>
              <input type="number" min={10} max={3000} value={numGroups}
                onChange={e => setNumGroups(Math.max(10, Math.min(3000, Number(e.target.value))))} />
            </div>
            <div className="rb-field">
              <label>每组期数 N（1 ~ {maxPeriods}）</label>
              <input type="number" min={1} max={maxPeriods} value={periodsN}
                onChange={e => setPeriodsN(Math.max(1, Math.min(maxPeriods, Number(e.target.value))))} />
              {periodsNInvalid && (
                <span className="rb-error">N 超出上限，当前 K={numbersK} 时 N 最大为 {maxPeriods}</span>
              )}
            </div>
            <div className="rb-field">
              <label>每期号码数 K（1-10）</label>
              <input type="number" min={1} max={10} value={numbersK}
                onChange={e => setNumbersK(Math.max(1, Math.min(10, Number(e.target.value))))} />
            </div>
            <div className="rb-field">
              <label>追投上限 M（≥ N = {periodsN}）</label>
              <input type="number" min={periodsN} value={chaseM}
                onChange={e => setChaseM(Math.max(periodsN, Number(e.target.value)))} />
            </div>
            <div className="rb-field">
              <label>基础单位（元）</label>
              <input type="number" min={0.01} step={0.01} value={baseUnit}
                onChange={e => setBaseUnit(Number(e.target.value))} />
            </div>
            <div className="rb-field">
              <label>马丁倍数</label>
              <input type="number" min={1} step={0.1} value={martinMultiplier}
                onChange={e => setMartinMultiplier(Math.max(1, Number(e.target.value)))} />
            </div>
            <div className="rb-field">
              <label>赔率</label>
              <input type="number" min={1.01} step={0.001} value={odds}
                onChange={e => setOdds(Number(e.target.value))} />
            </div>
            <div className="rb-field">
              <label>资金模式</label>
              <select value={fundMode} onChange={e => setFundMode(e.target.value as 'shared_total' | 'per_group')}>
                <option value="per_group">每组独立余额</option>
                <option value="shared_total">所有组共享余额</option>
              </select>
            </div>
            {fundMode === 'shared_total' ? (
              <div className="rb-field">
                <label>总余额（元）</label>
                <input type="number" min={1} value={totalBalance}
                  onChange={e => setTotalBalance(Number(e.target.value))} />
              </div>
            ) : (
              <div className="rb-field">
                <label>每组余额（元）</label>
                <input type="number" min={1} value={perGroupBalance}
                  onChange={e => setPerGroupBalance(Number(e.target.value))} />
              </div>
            )}
          </div>
          <div className="rb-hint">
            当前 K={numbersK} 时，每组可用的 (球号+号码) 组合共 <strong>{maxPeriods}</strong> 种，N 不能超过此值。
            组数 G 最多可生成数亿种不重复方案，3000 组远未触及上限。
          </div>
          <button className="rb-btn-primary" onClick={handleGenerate} disabled={generating || periodsNInvalid}>
            {generating ? '生成中...' : '生成方案 →'}
          </button>
        </div>
      )}

      {/* Phase 2 */}
      {phase >= 2 && planData && (
        <div className="rb-card">
          <div className="rb-card-header">
            <h2>方案预览</h2>
            <button className="rb-btn-ghost" onClick={() => setPhase(1)}>重新配置</button>
          </div>
          <div className="rb-plan-meta">
            <span>方案 ID：<strong>{planData.plan_set_id}</strong></span>
            <span>总组数：<strong>{planData.num_groups}</strong></span>
            <span>每组 {planData.periods_per_group} 期，每期 {planData.numbers_per_period} 个号</span>
          </div>
          <PlanPreview plans={planData.plans} />
          {phase === 2 && (
            <button className="rb-btn-primary" onClick={() => setPhase(3)}>
              继续 → 配置回测参数
            </button>
          )}
        </div>
      )}

      {/* Phase 3 */}
      {phase >= 3 && (
        <div className="rb-card">
          <div className="rb-card-header">
            <h2>
              回测执行
              {iterationDepth > 0 && filteredPlanIds && (
                <span className="rb-iter-tag">
                  ｜第 {iterationDepth} 层迭代（{filteredPlanIds.length} 组）
                </span>
              )}
            </h2>
            {!backtestRunning && (
              <button className="rb-btn-ghost" onClick={() => { setPhase(2); setTaskResult(null); }}>
                返回方案
              </button>
            )}
          </div>

          {/* 历史数据状态栏 */}
          <div className="rb-history-bar">
            <div className="rb-history-info">
              {historyStatus ? (
                <>
                  历史数据：<strong>{historyStatus.total_count.toLocaleString()}</strong> 条
                  {historyMinDate && historyMaxDate && (
                    <> | 可用区间：<strong>{historyMinDate}</strong> ~ <strong>{historyMaxDate}</strong></>
                  )}
                  {historyStatus.gap_count > 0 && (
                    <span className="rb-gap-warn"> | 缺失 {historyStatus.gap_count} 期</span>
                  )}
                  {historyStatus.syncing && (
                    <span className="rb-syncing"> | 同步中，请稍候...</span>
                  )}
                </>
              ) : '加载历史数据信息...'}
            </div>
            <button
              className="rb-btn-ghost rb-btn-sm"
              onClick={handleFillHistory}
              disabled={filling || historyStatus?.syncing || historyStatus?.gap_count === 0}
            >
              {filling || historyStatus?.syncing ? '同步中...' : historyStatus?.gap_count === 0 ? '数据已最新' : '同步数据'}
            </button>
          </div>

          <div className="rb-grid-2">
            <div className="rb-field">
              <label>开始日期</label>
              <input type="date" value={startDate}
                min={historyMinDate ?? undefined}
                max={historyMaxDate ?? fmtDate(today)}
                onChange={e => handleStartChange(e.target.value)} />
            </div>
            <div className="rb-field">
              <label>结束日期</label>
              <input type="date" value={endDate}
                min={historyMinDate ?? undefined}
                max={historyMaxDate ?? fmtDate(today)}
                onChange={e => handleEndChange(e.target.value)} />
            </div>
          </div>
          {dateError && <div className="rb-error">{dateError}</div>}
          <div className="rb-hint">每次最多选择 1 天的数据区间，建议在多个不同日期反复验证。</div>

          {backtestRunning && (
            <div className="rb-progress">
              <div className="rb-progress-bar" style={{ width: `${progress}%` }} />
              <span className="rb-progress-label">回测中 {progress}%</span>
            </div>
          )}

          {!backtestRunning && (
            <button className="rb-btn-primary" onClick={handleRunBacktest}
              disabled={!!dateError || !startDate || !endDate}>
              开始回测
            </button>
          )}

          {/* Aggregate summary */}
          {result && (
            <div className="rb-result-summary">
              <div className="rb-summary-grid">
                <SummaryItem label="总组数" value={String(planData?.num_groups ?? '-')} />
                <SummaryItem label="爆掉组数" value={String(busted.length)} valueClass="pnl-negative" />
                <SummaryItem label="存活组数" value={String(survived.length)} valueClass="pnl-positive" />
                <SummaryItem label="总期数" value={String(result.processed_issues)} />
                <SummaryItem label="总胜率" value={`${(result.total_win_rate * 100).toFixed(1)}%`} />
                <SummaryItem
                  label="最大回撤"
                  value={`${fmtFen(result.total_max_drawdown)} 元`}
                  valueClass="pnl-negative"
                />
              </div>
              <EquityCurveChart curve={result.total_equity_curve} />
            </div>
          )}
        </div>
      )}

      {/* Phase 4 */}
      {phase === 4 && result && (
        <div className="rb-card">
          <h2>选择组 → 创建策略</h2>
          <div className="rb-group-tabs">
            <button type="button"
              className={`rb-tab ${selectTab === 'survived' ? 'rb-tab-active' : ''}`}
              onClick={() => setSelectTab('survived')}>
              存活组 ({survived.length})
            </button>
            <button type="button"
              className={`rb-tab ${selectTab === 'busted' ? 'rb-tab-active' : ''}`}
              onClick={() => setSelectTab('busted')}>
              爆掉组 ({busted.length})
            </button>
          </div>

          <div className="rb-quick-select">
            <button type="button" className="rb-btn-ghost rb-btn-sm"
              onClick={() => selectAll(shownGroups)}>全选当前</button>
            <button type="button" className="rb-btn-ghost rb-btn-sm"
              onClick={() => deselectAll(shownGroups)}>取消当前</button>
            <button type="button" className="rb-btn-ghost rb-btn-sm"
              onClick={() => selectAll(survived)}>全选存活</button>
            <button type="button" className="rb-btn-ghost rb-btn-sm"
              onClick={() => setSelectedGroups(new Set())}>全不选</button>
          </div>

          <div className="rb-group-list">
            {shownGroups.map(g => (
              <GroupRow key={g.group_id} group={g}
                selected={selectedGroups.has(g.group_id)}
                onToggle={toggleGroup} />
            ))}
            {shownGroups.length === 0 && (
              <div className="rb-empty">无{selectTab === 'survived' ? '存活' : '爆掉'}组</div>
            )}
          </div>

          <div className={`rb-selection-status ${tooFew || tooMany ? 'rb-selection-warn' : ''}`}>
            已选 {selectedCount} 组
            {tooFew && ` （最少选择 10 组）`}
            {tooMany && ` （最多选择 1500 组）`}
            {iterationDepth > 0 && (
              <span className="rb-iter-tag"> · 第 {iterationDepth} 层迭代（最多 {MAX_ITERATION_DEPTH} 层）</span>
            )}
          </div>

          <div className="rb-phase4-actions">
            <button
              className="rb-btn-primary"
              onClick={handleCreateStrategy}
              disabled={!selectionValid}
            >
              创建AI马丁策略 →
            </button>
            <button
              className="rb-btn-ghost"
              disabled={selectedCount === 0 || iterationDepth >= MAX_ITERATION_DEPTH}
              onClick={() => {
                if (iterationDepth >= MAX_ITERATION_DEPTH) {
                  showToast(`已达最大迭代深度 ${MAX_ITERATION_DEPTH} 层，请重置后再次开始`);
                  return;
                }
                setFilteredPlanIds(Array.from(selectedGroups));
                setIterationDepth(d => d + 1);
                setSelectedGroups(new Set());
                setTaskResult(null);
                setProgress(0);
                setPhase(3);
              }}
            >
              用所选 {selectedCount} 组换日期再回测 ↻
            </button>
            {iterationDepth > 0 && (
              <button
                className="rb-btn-ghost"
                onClick={() => {
                  setFilteredPlanIds(null);
                  setIterationDepth(0);
                  setSelectedGroups(new Set());
                  setTaskResult(null);
                  setProgress(0);
                  setPhase(3);
                }}
              >
                重置回完整方案
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PlanPreview
// ---------------------------------------------------------------------------

function PlanPreview({ plans }: { plans: PlanSchema[] }) {
  const [expanded, setExpanded] = useState(false);
  const preview = expanded ? plans.slice(0, 10) : plans.slice(0, 3);

  return (
    <div className="rb-plan-preview">
      {preview.map(plan => (
        <div key={plan.group_id} className="rb-plan-group">
          <span className="rb-plan-gid">第 {plan.group_id + 1} 组</span>
          {plan.periods.map(p => (
            <span key={p.period_index} className="rb-plan-period">
              B{p.ball}:[{p.numbers.join(',')}]
            </span>
          ))}
        </div>
      ))}
      {plans.length > 3 && (
        <button type="button" className="rb-btn-ghost rb-btn-sm"
          onClick={() => setExpanded(e => !e)}>
          {expanded ? `折叠（共 ${plans.length} 组）` : `展开更多（共 ${plans.length} 组）`}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SummaryItem
// ---------------------------------------------------------------------------

function SummaryItem({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rb-summary-item">
      <span className="rb-summary-label">{label}</span>
      <span className={`rb-summary-value ${valueClass ?? ''}`}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EquityCurveChart
// ---------------------------------------------------------------------------

function EquityCurveChart({ curve }: { curve: number[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || curve.length < 2) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width, H = canvas.height;
    const pad = { top: 20, right: 20, bottom: 30, left: 60 };
    const pW = W - pad.left - pad.right, pH = H - pad.top - pad.bottom;
    const minV = Math.min(...curve), maxV = Math.max(...curve);
    const range = maxV - minV || 1;
    const toX = (i: number) => pad.left + (i / (curve.length - 1)) * pW;
    const toY = (v: number) => pad.top + pH - ((v - minV) / range) * pH;
    ctx.clearRect(0, 0, W, H);
    // zero line
    if (minV < 0 && maxV > 0) {
      ctx.strokeStyle = '#555'; ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(pad.left, toY(0)); ctx.lineTo(W - pad.right, toY(0)); ctx.stroke();
    }
    // curve
    const lastVal = curve[curve.length - 1] ?? 0;
    ctx.strokeStyle = lastVal >= 0 ? '#22c55e' : '#ef4444';
    ctx.lineWidth = 1.5; ctx.beginPath();
    curve.forEach((v, i) => { const x = toX(i), y = toY(v); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.stroke();
    // labels
    ctx.fillStyle = '#e0e0e0'; ctx.font = '11px sans-serif'; ctx.textAlign = 'right';
    ctx.fillText(maxV.toFixed(1), pad.left - 4, pad.top + 4);
    ctx.fillText(minV.toFixed(1), pad.left - 4, pad.top + pH + 4);
  }, [curve]);

  return (
    <div className="rb-chart">
      <h3>总权益曲线</h3>
      <canvas ref={canvasRef} width={600} height={220} className="rb-canvas" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// GroupRow
// ---------------------------------------------------------------------------

interface GroupRowProps {
  group: GroupResultSchema;
  selected: boolean;
  onToggle: (gid: number) => void;
}

function GroupRow({ group, selected, onToggle }: GroupRowProps) {
  const pnlClass = group.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
  return (
    <div
      className={`rb-group-row ${selected ? 'rb-group-row-selected' : ''}`}
      onClick={() => onToggle(group.group_id)}
      role="checkbox"
      aria-checked={selected}
      tabIndex={0}
      onKeyDown={e => e.key === ' ' && onToggle(group.group_id)}
    >
      <span className="rb-group-check">{selected ? '☑' : '☐'}</span>
      <span className="rb-group-id">第 {group.group_id + 1} 组</span>
      <span className={`rb-group-pnl ${pnlClass}`}>
        {group.total_pnl >= 0 ? '+' : ''}{fmtFen(group.total_pnl)} 元
      </span>
      <span className="rb-group-wr">胜率 {(group.win_rate * 100).toFixed(1)}%</span>
      <span className="rb-group-dd">回撤 {fmtFen(group.max_drawdown)}</span>
      {group.abandoned && <span className="rb-group-bust">已爆</span>}
    </div>
  );
}
