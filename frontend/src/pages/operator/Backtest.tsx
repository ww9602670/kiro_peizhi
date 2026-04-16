/**
 * 回测页面
 * - 历史数据状态 + 补全按钮
 * - 参数配置表单（日期选择，最多3个月）
 * - 结果展示（汇总 + 盈亏曲线）
 * - 历史任务列表
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { isApiError } from '@/api/request';
import {
  createBacktestTask,
  listBacktestTasks,
  getBacktestTask,
  getHistoryStatus,
  fillHistory,
} from '@/api/backtest';
import type {
  BacktestCreate,
  BacktestInfo,
  BacktestResultSchema,
  HistoryDataStatus,
} from '@/api/backtest';
import Toast from '@/components/Toast';
import PlayCodeMultiSelect from '@/components/PlayCodeMultiSelect';
import { getKeyCodeName } from '@/utils/key-code-map';
import { useToast } from '@/hooks/useToast';
import './Backtest.css';

/** 格式化日期为 YYYY-MM-DD */
function fmtDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export default function Backtest() {
  const [historyStatus, setHistoryStatus] = useState<HistoryDataStatus | null>(null);
  const [tasks, setTasks] = useState<BacktestInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [filling, setFilling] = useState(false);
  const [selectedTask, setSelectedTask] = useState<BacktestInfo | null>(null);
  const { messages, showToast, removeToast } = useToast();

  const fetchHistory = useCallback(async () => {
    try {
      const res = await getHistoryStatus();
      if (res.data) setHistoryStatus(res.data);
    } catch { /* ignore */ }
  }, []);

  const fetchTasks = useCallback(async (p = 1) => {
    try {
      const res = await listBacktestTasks(p, 10);
      if (res.data) {
        setTasks(res.data.items);
        setTotal(res.data.total);
        setPage(p);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchHistory(); fetchTasks(); }, [fetchHistory, fetchTasks]);

  // 自动刷新历史状态：同步中 3 秒，否则 30 秒
  useEffect(() => {
    const interval = historyStatus?.syncing ? 3000 : 30000;
    const timer = setInterval(fetchHistory, interval);
    return () => clearInterval(timer);
  }, [fetchHistory, historyStatus?.syncing]);

  const handleFillHistory = async () => {
    setFilling(true);
    try {
      const res = await fillHistory();
      const d = res.data as { total_inserted?: number } | null;
      showToast(`补全完成，新增 ${d?.total_inserted ?? 0} 条`);
      fetchHistory();
    } catch (err) {
      showToast(isApiError(err) ? err.message : '补全失败');
    } finally {
      setFilling(false);
    }
  };

  const handleCreate = async (data: BacktestCreate) => {
    setLoading(true);
    try {
      const res = await createBacktestTask(data);
      showToast('回测任务已创建');
      if (res.data) {
        setSelectedTask(res.data);
        pollTask(res.data.id);
      }
      fetchTasks();
    } catch (err) {
      showToast(isApiError(err) ? err.message : '创建失败');
    } finally {
      setLoading(false);
    }
  };

  const pollTask = async (id: number) => {
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const res = await getBacktestTask(id);
        if (res.data) {
          setSelectedTask(res.data);
          fetchTasks(page);
          if (res.data.status === 'completed' || res.data.status === 'failed') {
            if (res.data.status === 'failed') showToast(res.data.error_message || '回测失败');
            return;
          }
        }
      } catch { return; }
    }
  };

  const handleSelectTask = async (id: number) => {
    try {
      const res = await getBacktestTask(id);
      if (res.data) setSelectedTask(res.data);
    } catch (err) {
      showToast(isApiError(err) ? err.message : '加载失败');
    }
  };

  return (
    <div className="backtest-page">
      <Toast messages={messages} onRemove={removeToast} />
      <h1 className="backtest-title">策略回测</h1>

      <div className="history-status-bar">
        <div className="history-status-info">
          {historyStatus ? (
            <>
              历史数据：{historyStatus.total_count.toLocaleString()} 条
              {historyStatus.max_issue && ` | 最新期号：${historyStatus.max_issue}`}
              {(historyStatus.gap_count ?? 0) > 0 && <span className="gap-warn"> | 缺失 {historyStatus.gap_count} 期</span>}
              {historyStatus.syncing && <span className="sync-indicator sync-running"> | 数据同步中，请稍候...</span>}
              {historyStatus.last_sync_time && (
                <span className="last-sync-info"> | 上次同步：{historyStatus.last_sync_time}
                  {historyStatus.last_sync_inserted != null && ` (+${historyStatus.last_sync_inserted}条)`}
                </span>
              )}
            </>
          ) : '加载中...'}
        </div>
        {historyStatus && (
          <button type="button" className="fill-btn" onClick={handleFillHistory}
            disabled={filling || historyStatus.syncing || (historyStatus.gap_count ?? 0) === 0}
            title={historyStatus.syncing ? '同步中' : (historyStatus.gap_count ?? 0) === 0 ? '数据已完整，无需补全' : ''}>
            {filling ? '补全中...' : historyStatus.syncing ? '同步中...' : (historyStatus.gap_count ?? 0) === 0 ? '数据已完整' : '补全到最新'}
          </button>
        )}
      </div>

      <BacktestForm onSubmit={handleCreate} loading={loading} historyStatus={historyStatus} />

      {selectedTask && selectedTask.result && (
        <BacktestResultView result={selectedTask.result} strategyType={selectedTask.strategy_type} />
      )}
      {selectedTask && selectedTask.status === 'running' && (
        <div className="backtest-running">回测运行中...</div>
      )}

      <BacktestHistory
        tasks={tasks} total={total} page={page}
        onPageChange={fetchTasks} onSelect={handleSelectTask}
        selectedId={selectedTask?.id}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// BacktestForm — 日期选择，默认最近1个月，最多3个月
// ---------------------------------------------------------------------------

interface BacktestFormProps {
  onSubmit: (data: BacktestCreate) => void;
  loading: boolean;
  historyStatus: HistoryDataStatus | null;
}

function BacktestForm({ onSubmit, loading }: BacktestFormProps) {
  const [strategyType, setStrategyType] = useState<'flat' | 'martin'>('flat');
  const [keyCodes, setKeyCodes] = useState<string[]>(['DX1']);
  const [baseAmount, setBaseAmount] = useState('1');
  const [martinSeq, setMartinSeq] = useState('1,2,4,8,16');
  const [oddsInput, setOddsInput] = useState('2.053');

  // 默认日期：最近1个月
  const today = new Date();
  const oneMonthAgo = new Date(today);
  oneMonthAgo.setMonth(oneMonthAgo.getMonth() - 1);
  const [startDate, setStartDate] = useState(fmtDate(oneMonthAgo));
  const [endDate, setEndDate] = useState(fmtDate(today));

  // 最多3个月限制
  const threeMonthsAgo = new Date(today);
  threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
  const minDate = fmtDate(threeMonthsAgo);
  const maxDate = fmtDate(today);

  const [dateError, setDateError] = useState('');

  const validateDates = (s: string, e: string) => {
    if (!s || !e) { setDateError(''); return; }
    const sd = new Date(s), ed = new Date(e);
    if (ed < sd) { setDateError('结束日期不能早于开始日期'); return; }
    const diffMs = ed.getTime() - sd.getTime();
    const diffDays = diffMs / (1000 * 60 * 60 * 24);
    if (diffDays > 93) { setDateError('日期范围不能超过3个月'); return; }
    setDateError('');
  };

  const handleStartChange = (v: string) => { setStartDate(v); validateDates(v, endDate); };
  const handleEndChange = (v: string) => { setEndDate(v); validateDates(startDate, v); };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (dateError) return;
    const codes = keyCodes;
    if (codes.length === 0) return;
    const oddsVal = parseFloat(oddsInput);
    const oddsMap: Record<string, number> = {};
    for (const kc of codes) oddsMap[kc] = oddsVal;

    const data: BacktestCreate = {
      strategy_type: strategyType,
      key_codes: codes,
      base_amount: parseFloat(baseAmount),
      odds_map: oddsMap,
      start_date: startDate,
      end_date: endDate,
    };
    if (strategyType === 'martin') {
      data.martin_sequence = martinSeq.split(',').map(s => parseFloat(s.trim()));
    }
    onSubmit(data);
  };

  return (
    <form className="backtest-form" onSubmit={handleSubmit}>
      <div className="form-row">
        <label htmlFor="bt-strategy-type">策略类型</label>
        <select id="bt-strategy-type" value={strategyType} onChange={e => setStrategyType(e.target.value as 'flat' | 'martin')}>
          <option value="flat">平注</option>
          <option value="martin">马丁</option>
        </select>
      </div>
      <div className="form-row">
        <label>玩法</label>
        <PlayCodeMultiSelect value={keyCodes} onChange={setKeyCodes} disabled={loading} />
      </div>
      <div className="form-row">
        <label htmlFor="bt-base-amount">基础金额（元）</label>
        <input id="bt-base-amount" type="number" step="0.01" min="0.01" value={baseAmount} onChange={e => setBaseAmount(e.target.value)} />
      </div>
      {strategyType === 'martin' && (
        <div className="form-row">
          <label htmlFor="bt-martin-seq">马丁序列</label>
          <input id="bt-martin-seq" value={martinSeq} onChange={e => setMartinSeq(e.target.value)} placeholder="1,2,4,8,16" />
        </div>
      )}
      <div className="form-row">
        <label htmlFor="bt-odds">赔率</label>
        <input id="bt-odds" type="number" step="0.001" min="0.001" value={oddsInput} onChange={e => setOddsInput(e.target.value)} />
      </div>
      <div className="form-row-group">
        <div className="form-row">
          <label htmlFor="bt-start-date">开始日期</label>
          <input id="bt-start-date" type="date" value={startDate} min={minDate} max={maxDate}
            onChange={e => handleStartChange(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="bt-end-date">结束日期</label>
          <input id="bt-end-date" type="date" value={endDate} min={minDate} max={maxDate}
            onChange={e => handleEndChange(e.target.value)} />
        </div>
      </div>
      {dateError && <div className="form-error">{dateError}</div>}
      <button type="submit" className="backtest-submit-btn" disabled={loading || !!dateError}>
        {loading ? '回测中...' : '开始回测'}
      </button>
    </form>
  );
}


// ---------------------------------------------------------------------------
// BacktestResultView
// ---------------------------------------------------------------------------

interface BacktestResultViewProps {
  result: BacktestResultSchema;
  strategyType: string;
}

function BacktestResultView({ result, strategyType }: BacktestResultViewProps) {
  const pnlClass = result.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';

  return (
    <div className="backtest-result">
      <h2>回测结果</h2>
      <div className="result-summary">
        <div className="summary-item">
          <span className="summary-label">总盈亏</span>
          <span className={`summary-value ${pnlClass}`}>{result.total_pnl.toFixed(2)} 元</span>
        </div>
        <div className="summary-item">
          <span className="summary-label">总投注</span>
          <span className="summary-value">{result.total_bet_amount.toFixed(2)} 元</span>
        </div>
        <div className="summary-item">
          <span className="summary-label">总期数</span>
          <span className="summary-value">{result.total_issues}</span>
        </div>
        <div className="summary-item">
          <span className="summary-label">胜率</span>
          <span className="summary-value">{(result.win_rate * 100).toFixed(1)}%</span>
        </div>
        <div className="summary-item">
          <span className="summary-label">最大连亏</span>
          <span className="summary-value">{result.max_consecutive_loss} 期</span>
        </div>
        <div className="summary-item">
          <span className="summary-label">最大回撤</span>
          <span className="summary-value">{result.max_drawdown.toFixed(2)} 元</span>
        </div>
        {result.max_martin_level != null && (
          <div className="summary-item">
            <span className="summary-label">最大马丁层级</span>
            <span className="summary-value">{result.max_martin_level}</span>
          </div>
        )}
      </div>
      {result.records.length > 0 && <PnlChart records={result.records} />}
      {strategyType === 'martin' && result.martin_level_dist && <MartinLevelChart dist={result.martin_level_dist} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PnlChart (Canvas)
// ---------------------------------------------------------------------------

function PnlChart({ records }: { records: BacktestResultSchema['records'] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || records.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width, H = canvas.height;
    const pad = { top: 20, right: 20, bottom: 30, left: 60 };
    const plotW = W - pad.left - pad.right, plotH = H - pad.top - pad.bottom;
    const values = records.map(r => r.cumulative_pnl);
    const minV = Math.min(0, ...values), maxV = Math.max(0, ...values);
    const range = maxV - minV || 1;
    const toX = (i: number) => pad.left + (i / (records.length - 1 || 1)) * plotW;
    const toY = (v: number) => pad.top + plotH - ((v - minV) / range) * plotH;

    ctx.clearRect(0, 0, W, H);
    // 零线
    ctx.strokeStyle = '#555'; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(pad.left, toY(0)); ctx.lineTo(W - pad.right, toY(0)); ctx.stroke();
    // 曲线
    ctx.strokeStyle = values[values.length - 1] >= 0 ? '#22c55e' : '#ef4444';
    ctx.lineWidth = 1.5; ctx.beginPath();
    for (let i = 0; i < values.length; i++) {
      const x = toX(i), y = toY(values[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    // Y 轴标签
    ctx.fillStyle = '#e0e0e0'; ctx.font = '11px sans-serif'; ctx.textAlign = 'right';
    ctx.fillText(maxV.toFixed(1), pad.left - 5, pad.top + 4);
    ctx.fillText(minV.toFixed(1), pad.left - 5, pad.top + plotH + 4);
    ctx.fillText('0', pad.left - 5, toY(0) + 4);
  }, [records]);

  return (
    <div className="chart-container">
      <h3>盈亏曲线</h3>
      <canvas ref={canvasRef} width={600} height={250} className="pnl-canvas" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// MartinLevelChart (Canvas)
// ---------------------------------------------------------------------------

function MartinLevelChart({ dist }: { dist: Record<number, number> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const entries = Object.entries(dist).map(([k, v]) => [Number(k), v] as [number, number]).sort((a, b) => a[0] - b[0]);
    if (entries.length === 0) return;
    const W = canvas.width, H = canvas.height;
    const pad = { top: 20, right: 20, bottom: 30, left: 50 };
    const plotW = W - pad.left - pad.right, plotH = H - pad.top - pad.bottom;
    const maxCount = Math.max(...entries.map(e => e[1]));
    const barW = Math.min(40, plotW / entries.length - 4);
    ctx.clearRect(0, 0, W, H);
    entries.forEach(([level, count], i) => {
      const x = pad.left + (i + 0.5) * (plotW / entries.length) - barW / 2;
      const barH = (count / (maxCount || 1)) * plotH;
      const y = pad.top + plotH - barH;
      ctx.fillStyle = '#3b82f6'; ctx.fillRect(x, y, barW, barH);
      ctx.fillStyle = '#e0e0e0'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(String(level), x + barW / 2, pad.top + plotH + 15);
      ctx.fillText(String(count), x + barW / 2, y - 4);
    });
  }, [dist]);

  return (
    <div className="chart-container">
      <h3>马丁层级分布</h3>
      <canvas ref={canvasRef} width={400} height={200} className="martin-canvas" />
    </div>
  );
}


// ---------------------------------------------------------------------------
// BacktestHistory
// ---------------------------------------------------------------------------

interface BacktestHistoryProps {
  tasks: BacktestInfo[];
  total: number;
  page: number;
  onPageChange: (p: number) => void;
  onSelect: (id: number) => void;
  selectedId?: number;
}

function BacktestHistory({ tasks, total, page, onPageChange, onSelect, selectedId }: BacktestHistoryProps) {
  const totalPages = Math.ceil(total / 10) || 1;
  const statusLabel = (s: string) => {
    switch (s) {
      case 'pending': return '等待中';
      case 'running': return '运行中';
      case 'completed': return '已完成';
      case 'failed': return '失败';
      default: return s;
    }
  };

  return (
    <div className="backtest-history">
      <h2>历史任务</h2>
      {tasks.length === 0 ? (
        <div className="history-empty">暂无回测任务</div>
      ) : (
        <>
          <div className="history-list">
            {tasks.map(t => (
              <div key={t.id} className={`history-item ${selectedId === t.id ? 'selected' : ''}`}
                onClick={() => onSelect(t.id)} role="button" tabIndex={0}
                onKeyDown={e => e.key === 'Enter' && onSelect(t.id)}>
                <div className="history-item-header">
                  <span className="history-type">{t.strategy_type === 'martin' ? '马丁' : '平注'}</span>
                  <span className={`history-status status-${t.status}`}>{statusLabel(t.status)}</span>
                </div>
                <div className="history-item-detail">
                  {t.key_codes.map(c => getKeyCodeName(c)).join(', ')} | {t.start_issue} ~ {t.end_issue}
                </div>
                <div className="history-item-meta">
                  {t.created_at}
                  {t.result && (
                    <span className={t.result.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}>
                      {' '}{t.result.total_pnl >= 0 ? '+' : ''}{t.result.total_pnl.toFixed(2)} 元
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          {totalPages > 1 && (
            <div className="history-pagination">
              <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>上一页</button>
              <span>{page} / {totalPages}</span>
              <button type="button" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>下一页</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
