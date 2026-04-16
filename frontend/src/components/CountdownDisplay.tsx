/**
 * Countdown display component
 * - Shows previous issue number + lottery wave-color result
 * - Shows close/open countdown timers
 * - Shows current lottery state (open/closed/drawing)
 */
import { useLotteryCountdown } from '@/hooks/useLotteryCountdown';
import { STATE_DISPLAY_MAP, LotteryStateEnum } from '@/types/api/lottery';
import './CountdownDisplay.css';

type WaveTone = 'red' | 'green' | 'blue' | 'special' | 'unknown';

const BALL_RED = new Set([3, 6, 9]);
const BALL_GREEN = new Set([1, 4, 7]);
const BALL_BLUE = new Set([2, 5, 8]);
const BALL_SPECIAL = new Set([0]);

const SUM_RED = new Set([3, 6, 9, 12, 15, 18, 21, 24]);
const SUM_GREEN = new Set([1, 4, 7, 10, 16, 19, 22, 25]);
const SUM_BLUE = new Set([2, 5, 8, 11, 17, 20, 23, 26]);
const SUM_SPECIAL = new Set([0, 13, 14, 27]);

function parseBalls(result: string): number[] | null {
  if (!result || !result.trim()) return null;
  const parts = result.split(',').map((item) => item.trim());
  if (parts.length < 3) return null;
  const balls = parts.slice(0, 3).map((item) => Number.parseInt(item, 10));
  if (balls.some((value) => Number.isNaN(value) || value < 0 || value > 9)) {
    return null;
  }
  return balls;
}

function getBallWave(value: number): WaveTone {
  if (BALL_RED.has(value)) return 'red';
  if (BALL_GREEN.has(value)) return 'green';
  if (BALL_BLUE.has(value)) return 'blue';
  if (BALL_SPECIAL.has(value)) return 'special';
  return 'unknown';
}

function getSumWave(value: number): WaveTone {
  if (SUM_RED.has(value)) return 'red';
  if (SUM_GREEN.has(value)) return 'green';
  if (SUM_BLUE.has(value)) return 'blue';
  if (SUM_SPECIAL.has(value)) return 'special';
  return 'unknown';
}

function ResultBalls({ result }: { result: string }) {
  const balls = parseBalls(result);
  if (!balls) return <span className="no-result">等待开奖</span>;
  const sum = balls[0] + balls[1] + balls[2];
  return (
    <span className="wave-result">
      {balls.map((value, index) => (
        <span key={index} className="wave-item">
          <span className="wave-label">球{index + 1}</span>
          <span className={`wave-value wave-${getBallWave(value)}`}>{value}</span>
        </span>
      ))}
      <span className="wave-item">
        <span className="wave-label">和值</span>
        <span className={`wave-value wave-${getSumWave(sum)}`}>{sum}</span>
      </span>
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
