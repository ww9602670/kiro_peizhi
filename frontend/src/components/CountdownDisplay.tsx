/**
 * Countdown display component
 * - Shows previous issue number + lottery result balls
 * - Shows close/open countdown timers
 * - Shows current lottery state (open/closed/drawing)
 */
import { useLotteryCountdown } from '@/hooks/useLotteryCountdown';
import { STATE_DISPLAY_MAP, LotteryStateEnum } from '@/types/api/lottery';
import './CountdownDisplay.css';

/** Ball color palette — cycles through for each number */
const BALL_COLORS = ['#1677ff', '#52c41a', '#ff4d4f', '#faad14', '#722ed1', '#13c2c2', '#eb2f96'];

function ResultBalls({ result }: { result: string }) {
  if (!result || !result.trim()) return <span className="no-result">等待开奖</span>;
  const nums = result.split(',').map(s => s.trim());
  const sum = nums.reduce((a, b) => a + (parseInt(b, 10) || 0), 0);
  return (
    <span className="result-balls">
      {nums.map((n, i) => (
        <span key={i} className="ball" style={{ background: BALL_COLORS[i % BALL_COLORS.length] }}>
          {n}
        </span>
      ))}
      <span className="ball-sum">= {sum}</span>
    </span>
  );
}

export function CountdownDisplay() {
  const { data, closeCountdown, openCountdown, error, lastUpdateTime } = useLotteryCountdown();

  const stateDisplay = STATE_DISPLAY_MAP[data?.state as LotteryStateEnum] ?? STATE_DISPLAY_MAP[LotteryStateEnum.UNKNOWN];

  return (
    <div className="countdown-display">
      {error && (
        <div className="error-banner">
          {error} {lastUpdateTime && `(最后更新: ${lastUpdateTime.toLocaleTimeString()})`}
        </div>
      )}
      <div className="issue-row">
        <span className="issue-label">最新开奖：</span>
        <strong className="issue-number">{data?.pre_installments || '-'}</strong>
        <ResultBalls result={data?.pre_lottery_result || ''} />
      </div>
      <div className="countdown-row">
        <div className="countdown-item">
          <span>当前期号</span>
          <strong className="issue-current">{data?.installments || '-'}</strong>
        </div>
        <div className="countdown-item">
          <span>状态</span>
          <strong className={`state-value state-${stateDisplay.color}`}>{stateDisplay.label}</strong>
        </div>
        <div className="countdown-item">
          <span>封盘倒计时</span>
          <strong className="cd-value">{closeCountdown}<small>秒</small></strong>
        </div>
        <div className="countdown-item">
          <span>开奖倒计时</span>
          <strong className="cd-value">{openCountdown}<small>秒</small></strong>
        </div>
      </div>
    </div>
  );
}
