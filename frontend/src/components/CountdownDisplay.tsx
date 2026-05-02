/**
 * Countdown display component.
 */
import { useState } from 'react';
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
const COLLAPSED_RECENT_RESULT_COUNT = 3;
const WAITING_DRAW_DISPLAY = { label: '等待开奖', color: 'yellow' };

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

function formatRecentTime(value: string | null | undefined) {
  const text = value?.trim();
  if (!text) return '-';
  const timePart = text.includes(' ') ? text.split(' ').pop() : text;
  return timePart?.slice(0, 5) || text;
}

function HistoryResultBalls({ result, showSum = true }: { result: string; showSum?: boolean }) {
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
      {showSum && <span className={`recent-sum recent-sum-${getSumWave(sum)}`}>{sum}</span>}
    </div>
  );
}

export function CountdownDisplay({ platformType, recentResults }: CountdownDisplayProps = {}) {
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const { data, closeCountdown, openCountdown, error, lastUpdateTime } = useLotteryCountdown({
    platformType,
  });

  const fallbackStateDisplay =
    STATE_DISPLAY_MAP[data?.state as LotteryStateEnum] ??
    STATE_DISPLAY_MAP[LotteryStateEnum.UNKNOWN];
  const stateDisplay =
    data && closeCountdown <= 0 && openCountdown <= 0
      ? WAITING_DRAW_DISPLAY
      : data && closeCountdown <= 0
        ? STATE_DISPLAY_MAP[LotteryStateEnum.CLOSED]
        : fallbackStateDisplay;
  const historyResults = recentResults ?? [];
  const visibleHistoryResults = historyExpanded
    ? historyResults
    : historyResults.slice(0, COLLAPSED_RECENT_RESULT_COUNT);
  const canToggleHistory = historyResults.length > COLLAPSED_RECENT_RESULT_COUNT;

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

      {historyResults.length > 0 && (
        <section className="recent-results-panel" aria-label="recent-results">
          <div className="recent-results-header">
            <strong className="recent-results-title">历史开奖</strong>
            {canToggleHistory && (
              <button
                type="button"
                className="recent-results-toggle"
                aria-expanded={historyExpanded}
                onClick={() => setHistoryExpanded((expanded) => !expanded)}
              >
                {historyExpanded ? '收起' : `展开全部 (${historyResults.length})`}
              </button>
            )}
          </div>
          <div className="recent-results-list">
            {visibleHistoryResults.map((result) => {
              const tone = getSumWave(result.sum_value);
              const resultTime = result.open_time || result.created_at;
              return (
                <article
                  key={result.id}
                  className={`recent-result-card recent-result-card-${tone}`}
                >
                  <strong className="recent-result-issue">{result.issue}</strong>
                  <HistoryResultBalls result={result.open_result} showSum={false} />
                  <span className={`recent-result-sum-tag recent-result-sum-tag-${tone}`}>
                    {`和值 ${result.sum_value}`}
                  </span>
                  <time className="recent-result-time" title={resultTime}>
                    {formatRecentTime(resultTime)}
                  </time>
                </article>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
