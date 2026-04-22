/**
 * Countdown display component.
 */
import { useLotteryCountdown } from '@/hooks/useLotteryCountdown';
import { STATE_DISPLAY_MAP, LotteryStateEnum } from '@/types/api/lottery';
import type { RecentLotteryResult } from '@/types/api/dashboard';
import './CountdownDisplay.css';

interface CountdownDisplayProps {
  platformType?: string;
  recentResults?: RecentLotteryResult[];
}

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
  if (!balls) return <span className="no-result">暂无结果</span>;
  const sum = balls[0] + balls[1] + balls[2];
  return (
    <span className="wave-result">
      {balls.map((value, index) => (
        <span key={index} className="wave-item">
          <span className="wave-label">{`球${index + 1}`}</span>
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

function HistoryResultBalls({ result }: { result: string }) {
  const balls = parseBalls(result);
  if (!balls) return <span className="recent-result-empty">-</span>;
  const sum = balls[0] + balls[1] + balls[2];
  return (
    <div className="recent-result-balls">
      {balls.map((value, index) => (
        <span
          key={`${index}-${value}`}
          className={`recent-ball recent-ball-${getBallWave(value)}`}
        >
          {value}
        </span>
      ))}
      <span className={`recent-sum recent-sum-${getSumWave(sum)}`}>{sum}</span>
    </div>
  );
}

export function CountdownDisplay({ platformType, recentResults }: CountdownDisplayProps = {}) {
  const { data, closeCountdown, openCountdown, error, lastUpdateTime } = useLotteryCountdown({
    platformType,
  });

  const stateDisplay =
    STATE_DISPLAY_MAP[data?.state as LotteryStateEnum] ??
    STATE_DISPLAY_MAP[LotteryStateEnum.UNKNOWN];

  return (
    <div className="countdown-display">
      {error && (
        <div className="error-banner">
          {error}{' '}
          {lastUpdateTime && `(更新时间 ${lastUpdateTime.toLocaleTimeString()})`}
        </div>
      )}

      <div className="issue-row">
        <span className="issue-label">上期开奖</span>
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
          <strong className="cd-value">
            {closeCountdown}
            <small>秒</small>
          </strong>
        </div>
        <div className="countdown-item">
          <span>开奖倒计时</span>
          <strong className="cd-value">
            {openCountdown}
            <small>秒</small>
          </strong>
        </div>
      </div>

      {recentResults && recentResults.length > 0 && (
        <section className="recent-results-panel" aria-label="recent-results">
          <div className="recent-results-list">
            {recentResults.map((result) => {
              const tone = getSumWave(result.sum_value);
              return (
                <article
                  key={result.id}
                  className={`recent-result-card recent-result-card-${tone}`}
                >
                  <div className="recent-result-header">
                    <strong className="recent-result-issue">{result.issue}</strong>
                    <span className={`recent-result-sum-tag recent-result-sum-tag-${tone}`}>
                      {`和值 ${result.sum_value}`}
                    </span>
                  </div>
                  <HistoryResultBalls result={result.open_result} />
                  <div className="recent-result-footer">
                    <span className="recent-result-raw">{result.open_result || '-'}</span>
                    <time className="recent-result-time">
                      {result.open_time || result.created_at}
                    </time>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
