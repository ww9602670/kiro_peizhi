/**
 * 策略状态标签组件
 * - stopped: 灰色
 * - running: 绿色
 * - paused: 橙色
 * - error: 红色
 */

import './StrategyStatusTag.css';

interface StrategyStatusTagProps {
  status: string;
}

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  stopped: { label: '已停止', className: 'status-tag-stopped' },
  running: { label: '运行中', className: 'status-tag-running' },
  paused: { label: '已暂停', className: 'status-tag-paused' },
  error: { label: '异常', className: 'status-tag-error' },
};

export default function StrategyStatusTag({ status }: StrategyStatusTagProps) {
  const info = STATUS_MAP[status] ?? { label: status, className: 'status-tag-stopped' };

  return (
    <span className={`status-tag ${info.className}`}>
      {info.label}
    </span>
  );
}
